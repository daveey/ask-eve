import json
import sqlite3
import subprocess
import xml.etree.ElementTree as ET

import pytest

from pipeline_helpers import ROOT, rms, write_tone_wav
import d1_upsert as D
import produce_audio as P
import render_feed as F
import sqlite_apply
import stitch_segments as S

MD = """---
date: 2026-11-01
title: Test & "quotes"
people: [Elon Musk, Peter Thiel]
source: https://grok.com/share/abc
---

[10:00:00] David: Hello there.

[10:00:10] Eve: Let us talk about Elon Musk.

[10:00:20] David: And Thiel?

[10:00:30] Eve: Sure.
"""


def m4a(path, seconds, freq, creation=None):
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate=48000",
           "-c:a", "aac", "-b:a", "64k"]
    if creation:
        cmd += ["-metadata", f"creation_time={creation}"]
    subprocess.run(cmd + [str(path)], check=True)
    return path


def test_stitch_places_by_creationdate(tmp_path):
    a = m4a(tmp_path / "b.m4a", 2, 500, "2026-11-01T10:00:00Z")
    b = m4a(tmp_path / "a.m4a", 1, 700, "2026-11-01T10:00:05Z")
    r = S.stitch([b, a], tmp_path / "o")            # argument order reversed on purpose
    assert [s["offset"] for s in r["segments"]] == [0.0, 5.0]
    assert [s["placement"] for s in r["segments"]] == ["creationdate"] * 2
    (g,) = r["recording_gaps"]
    assert g["after_segment"] == 0 and g["end"] == 5.0 and g["seconds"] == pytest.approx(3.0, abs=0.05)
    assert r["processing_chain"].startswith("highpass=f=80")
    w = tmp_path / "o/david_48k.wav"
    assert rms(w, 0.2, 1.8) > 0.05 and rms(w, 2.3, 4.7) < 0.001 and rms(w, 5.1, 5.9) > 0.05
    assert r["recording_started_at"].startswith("2026-11-01T10:00:00")


def test_stitch_overlap_falls_back_to_sequential(tmp_path):
    a = m4a(tmp_path / "a.m4a", 3, 500, "2026-11-01T10:00:00Z")
    b = m4a(tmp_path / "b.m4a", 1, 700, "2026-11-01T10:00:01Z")   # starts before a ends
    r = S.stitch([a, b], tmp_path / "o", gap=2.0)
    assert r["segments"][1]["placement"] == "sequential-fallback" and r["segments"][1]["offset"] == pytest.approx(5.0, abs=0.05)


def test_produce_and_feed(tmp_path):
    tr = tmp_path / "2026-11-01_test.md"
    tr.write_text(MD)
    mix = tmp_path / "mix.wav"
    write_tone_wav(mix, 12.0, [(0.5, 3.0, 300), (4.0, 6.0, 500), (7.0, 9.0, 300), (10.0, 11.0, 500)])
    timeline = {"recording_method": "tts-reconstructed", "placeholder_audio": False, "tts": "xai_tts voice=eve",
                "narrator": "xai_tts voice=rex", "disclaimer": {"start": 0.5}, "missing_turns": [],
                "turns": [{"i": 0, "speaker": "David", "edited_start": 4.0, "edited_end": 6.0, "method": "tts-synthetic"},
                          {"i": 1, "speaker": "Eve", "edited_start": 7.0, "edited_end": 9.0, "method": "tts-synthetic"},
                          {"i": 2, "speaker": "David", "edited_start": 9.2, "edited_end": 9.8, "method": "tts-synthetic"},
                          {"i": 3, "speaker": "Eve", "edited_start": 10.0, "edited_end": 11.0, "method": "tts-synthetic"}]}
    man = P.produce(mix, tr, timeline, tmp_path / "p", min_chapter_gap=2.0)
    assert man["duration_seconds"] == 12 and man["id3_chapters"] == len(man["chapters"]) == 3
    assert man["chapters"][0]["title"] == "AI-reconstructed audio notice"
    assert man["chapters"][1] == {"startTime": 4.0, "title": "Elon Musk"}          # Eve's mention -> preceding David turn
    assert man["chapters"][2]["title"] == "Peter Thiel"
    assert abs(man["loudness_lufs"] + 16) < 1.5 and man["true_peak_dbfs"] <= -1.0
    assert man["ai_reconstructed"] and not man["placeholder_audio"]
    assert man["audio_url"] == "https://audio.askevepodcast.com/audio/en/2026-11-01_test.v1.mp3"
    assert json.loads((tmp_path / "p/chapters.json").read_text())["version"] == "1.2.0"

    db = tmp_path / "d.sqlite"
    sql = tmp_path / "u.sql"
    sql.write_text(D.build_sql(tr, tmp_path, "test", revoice=timeline, manifest=man))
    sqlite_apply.apply(db, ROOT / "migrations", sql)
    out = tmp_path / "feed.xml"
    assert F.main(["--db", str(db), "--out", str(out)]) == 0
    assert ET.parse(out).getroot().find("channel").find("item") is None            # drafts excluded by default
    assert F.main(["--db", str(db), "--out", str(out), "--include-drafts", "--show-notes-dir", str(tmp_path / "n")]) == 0
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    ch = ET.parse(out).getroot().find("channel")
    assert ch.find("itunes:owner/itunes:email", ns).text == "podcast@askevepodcast.com"
    assert ch.find("link").text == "https://askevepodcast.com"
    item = ch.find("item")
    assert item.find("title").text == 'Test & "quotes"'
    assert item.find("description").text.startswith("AI-reconstructed audio")
    assert item.find("enclosure").get("url") == man["audio_url"] and int(item.find("enclosure").get("length")) == man["audio_bytes"]
    notes = (tmp_path / "n/2026-11-01_test.md").read_text()
    assert "**AI-reconstructed audio.**" in notes and "00:00:04 Elon Musk" in notes


def test_feed_refuses_placeholder_manifest(tmp_path):
    db = tmp_path / "d.sqlite"
    sqlite_apply.apply(db, ROOT / "migrations")
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps({"placeholder_audio": True}))
    assert F.main(["--db", str(db), "--out", str(tmp_path / "f.xml"), "--manifests", str(m)]) == 4
    assert F.main(["--db", str(db), "--out", str(tmp_path / "f.xml"), "--manifests", str(m), "--allow-placeholder"]) == 0
