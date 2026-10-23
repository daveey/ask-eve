import json
import sqlite3

import pytest

from pipeline_helpers import ROOT, EP1
import d1_upsert as D
import sqlite_apply

MIG = (ROOT / "migrations/0001_init.sql").read_text()
SHA = "a" * 64


@pytest.fixture
def db():
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")   # D1 enforces FKs by default
    con.executescript(MIG)
    return con


def ins(con, **kw):
    row = {"slug": "s", "guid": "g", "date": "2026-10-23", "title": "t", "transcript_path": "episodes/x.md",
           "transcript_sha256": SHA, "transcript_bytes": 1} | kw
    con.execute(f"INSERT INTO episodes ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
    return con.execute("SELECT id FROM episodes WHERE slug=?", (row["slug"],)).fetchone()[0]


def test_defaults(db):
    eid = ins(db)
    assert db.execute("SELECT recording_method, ai_reconstructed, status FROM episodes WHERE id=?", (eid,)).fetchone() == ("mic-plus-tts", 0, "draft")


@pytest.mark.parametrize("kw", [
    {"recording_method": "tts-reconstructed"},                                   # no AI label
    {"recording_method": "tts-reconstructed", "ai_reconstructed": 1},            # label without disclosure text
    {"ai_reconstructed": 1, "ai_disclosure": ""},
    {"recording_method": "ios-screen-recording"},
    {"alignment_method": "magic"},
    {"alignment_coverage": 1.5},
    {"transcript_sha256": "abc"},
    {"status": "published"},                                                       # no audio / published_at
    {"raw_audio_format": "ogg"},
])
def test_episode_rejections(db, kw):
    with pytest.raises(sqlite3.IntegrityError):
        ins(db, **kw)


def test_ai_label_accepted(db):
    ins(db, recording_method="tts-reconstructed", ai_reconstructed=1, ai_disclosure="AI voices")


@pytest.mark.parametrize("row", [
    ("Eve", "asr-match", 120.0), ("David", "gap-placed", None), ("David", "tts-placed", None),
    ("Eve", "interpolated", 50.0), ("Narrator", "asr-match", 90.0), ("Eve", "guess", None),
])
def test_turn_offset_rejections(db, row):
    eid = ins(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO turn_offsets (episode_id, turn_index, speaker, method, score) VALUES (?,?,?,?,?)", (eid, 0, *row))


def test_turn_offset_end_before_start_and_fk(db):
    eid = ins(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO turn_offsets (episode_id, turn_index, speaker, method, raw_start_seconds, raw_end_seconds)"
                   " VALUES (?,0,'David','asr-match',5,4)", (eid,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO turn_offsets (episode_id, turn_index, speaker, method) VALUES (999,0,'David','fit')")
    db.execute("INSERT INTO turn_offsets (episode_id, turn_index, speaker, method) VALUES (?,0,'Eve','gap-placed')", (eid,))
    db.execute("DELETE FROM episodes WHERE id=?", (eid,))
    assert db.execute("SELECT count(*) FROM turn_offsets").fetchone()[0] == 0     # cascade


def synthetic_revoice(n_turns, speakers):
    t, turns = 2.0, []
    for i, sp in enumerate(speakers):
        turns.append({"i": i, "speaker": sp, "edited_start": t, "edited_end": t + 1, "method": "tts-synthetic"}); t += 1.5
    return {"recording_method": "tts-reconstructed", "tts": "xai_tts voice=eve", "narrator": "xai_tts voice=rex",
            "placeholder_audio": False, "turns": turns}


def test_upsert_synthetic_episode_zero_is_labelled_and_idempotent(db, tmp_path):
    from align_audio import parse_transcript
    turns = parse_transcript(EP1.read_text())
    rv = synthetic_revoice(len(turns), [t.speaker for t in turns])
    manifest = {"audio_key": "audio/en/x.v1.mp3", "audio_bytes": 1000, "audio_sha256": SHA, "audio_mime": "audio/mpeg",
                "duration_seconds": 500, "loudness_lufs": -16.0, "recording_method": "tts-reconstructed",
                "chapters": [{"startTime": 0, "title": "AI-reconstructed audio notice"}, {"startTime": 130.5, "title": "Elon Musk"}]}
    sql = D.build_sql(EP1, ROOT, "eve-david-podcast-planning", revoice=rv, manifest=manifest, episode_number=0)
    for _ in range(2):
        db.executescript(sql)
    row = db.execute("SELECT recording_method, ai_reconstructed, ai_disclosure, narrator_voice, tts_voice, audio_url, episode_number,"
                     " alignment_method, turn_count FROM episodes").fetchone()
    assert row[0] == "tts-reconstructed" and row[1] == 1 and "AI-reconstructed" in row[2]
    assert row[3] == "xai_tts voice=rex" and row[4] == "xai_tts voice=eve"
    assert row[5] == "https://audio.askevepodcast.com/audio/en/x.v1.mp3" and row[6] == 0 and row[7] == "tts-exact"
    assert db.execute("SELECT count(*) FROM turn_offsets").fetchone()[0] == row[8] == len(turns)
    assert db.execute("SELECT count(*) FROM chapters").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM episode_people").fetchone()[0] == 12


def test_upsert_mic_episode_uses_alignment(db):
    from align_audio import parse_transcript
    md = ROOT / "episodes/2026-10-23_eve-david-podcast-planning.md"
    turns = parse_transcript(md.read_text())
    al = {"alignment_method": "asr-david-gaps", "alignment_coverage": 0.9, "asr_model": "faster-whisper small int8",
          "sync_offset_seconds": 3.0, "turns": [
              {"i": t.index, "speaker": t.speaker, "raw_start": float(t.index), "raw_end": t.index + 0.5,
               "method": "asr-match" if t.speaker == "David" else "gap-placed", "score": 90.0 if t.speaker == "David" else None}
              for t in turns]}
    db.executescript(D.build_sql(md, ROOT, "eve-david-podcast-planning", alignment=al))
    assert db.execute("SELECT recording_method, ai_reconstructed, alignment_path FROM episodes").fetchone() == (
        "mic-plus-tts", 0, "alignment/2026-10-23_eve-david-podcast-planning.json")


def test_upsert_rejects_mismatched_offsets():
    with pytest.raises(ValueError):
        D.build_sql(EP1, ROOT, "x", revoice={"recording_method": "tts-reconstructed", "turns": []})


def test_sqlite_apply_is_rerunnable(tmp_path):
    sql = tmp_path / "u.sql"
    sql.write_text("INSERT INTO people (slug, name) VALUES ('a','A') ON CONFLICT(slug) DO NOTHING;")
    for _ in range(2):
        sqlite_apply.apply(tmp_path / "d.sqlite", ROOT / "migrations", sql)
    con = sqlite3.connect(tmp_path / "d.sqlite")
    assert con.execute("SELECT count(*) FROM people").fetchone()[0] == 1
