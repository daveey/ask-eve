from pathlib import Path
import hashlib
import json

import pytest

from pipeline_helpers import EP1, EP1_EXPORT_SHA, EP1_SHA, SHARE_JSON
import ingest_share as I

EP1_ID = "c2hhcmQtMg_***REDACTED***"
EP1_PEOPLE = ("David Sacks, Marc Andreessen, Ben Horowitz, Elon Musk, Chamath Palihapitiya, Jason Calacanis, "
              "Peter Thiel, Nancy Pelosi, Chan, Scott Wiener, Bari Weiss, Zohran Mamdani")


def test_committed_episode1_hash():
    assert hashlib.sha256(EP1.read_bytes()).hexdigest() == EP1_SHA


@pytest.mark.skipif(not SHARE_JSON.exists(), reason="episode-1 share JSON not on this machine")
def test_reproduces_episode1_byte_for_byte(tmp_path, capsys):
    rc = I.main([str(SHARE_JSON), "--share-id", EP1_ID, "--slug", "eve-david-podcast-planning",
                 "--title", "Eve-David podcast planning session", "--people", EP1_PEOPLE, "--out", str(tmp_path)])
    assert rc == 0
    info = json.loads(capsys.readouterr().out)
    out = tmp_path / "2026-10-23_eve-david-podcast-planning.md"
    assert info["sha256"] == EP1_EXPORT_SHA and info["turns"] == 304 and info["bytes"] == 151711
    # The committed transcript is the export minus a documented redaction (front-matter `redactions:`):
    # three Eve lines that carried a private notes-page URL. Every other line is byte-identical.
    exported, committed = out.read_text().split("\n"), EP1.read_text().split("\n")
    # The display title was renamed after publishing (front matter only); the export keeps the original title.
    assert "title: Eve-David podcast planning session" in exported and "title: First Encounter" in committed
    exported = ["title: First Encounter" if l == "title: Eve-David podcast planning session" else l for l in exported]
    # Context note + Topics list were added to the front matter after publishing.
    i = committed.index("topics:")
    assert committed[i - 1].startswith("context: ") and committed[i + 1].startswith("  - ")
    j = i + 1
    while committed[j].startswith("  - "):
        j += 1
    committed = committed[:i - 1] + committed[j:]
    assert [l for l in committed if l.startswith("redactions: ")] and len(committed) == len(exported) + 1
    committed = [l for l in committed if not l.startswith("redactions: ")]
    changed = [(a, b) for a, b in zip(exported, committed) if a != b]
    assert len(changed) == 3
    for a, b in changed:
        assert "canvas" in a and "canvas" not in b and "[link to a private notes page removed]" in b


def _share(*turns):
    return {"responses": [{"responseId": str(i), "sender": s, "createTime": t, "message": m} for i, (s, t, m) in enumerate(turns)]}


def test_render_sorts_by_createtime_and_labels(tmp_path):
    share = _share(("assistant", "2026-11-01T10:00:05.000Z", "  Hi David.  "),
                   ("human", "2026-11-01T10:00:00.000Z", "Hello Eve"))
    doc = I.render(share, "abc", "T", ["Peter Thiel"])
    assert doc.endswith("[10:00:00] David: Hello Eve\n\n[10:00:05] Eve: Hi David.\n")
    assert "note: Verbatim export of the shared Grok voice session (2 turns, 10:00-10:00 UTC)." in doc
    assert "people: [Peter Thiel]\nsource: https://grok.com/share/abc\n" in doc


def test_rejects_bad_inputs(tmp_path):
    with pytest.raises(I.IngestError):
        I.share_id_from_url("https://evil.example/share/x")
    with pytest.raises(I.IngestError):
        I.share_id_from_url("https://grok.com/share/x;rm -rf /")
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_share(("system", "2026-11-01T10:00:00.000Z", "x"))))
    with pytest.raises(I.IngestError):
        I.ingest(str(p), "ok", "T", [], tmp_path, share_id="abc")
    with pytest.raises(I.IngestError):
        I.ingest(str(p), "Bad Slug", "T", [], tmp_path, share_id="abc")
    with pytest.raises(I.IngestError):
        I.ingest(str(p), "ok", "T", [], tmp_path)          # saved JSON needs --share-id


def test_refuses_to_overwrite_different_content(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_share(("human", "2026-11-01T10:00:00.000Z", "Hello"))))
    I.ingest(str(p), "x", "T", [], tmp_path / "ep", share_id="abc")
    I.ingest(str(p), "x", "T", [], tmp_path / "ep", share_id="abc")      # identical: fine
    with pytest.raises(I.IngestError):
        I.ingest(str(p), "x", "Other title", [], tmp_path / "ep", share_id="abc")
    I.ingest(str(p), "x", "Other title", [], tmp_path / "ep", share_id="abc", force=True)


def test_url_source_uses_fetch(monkeypatch, tmp_path):
    share = _share(("human", "2026-11-01T10:00:00.000Z", "Hello"))
    seen = []
    monkeypatch.setattr(I, "fetch_share", lambda sid, timeout=60: seen.append(sid) or json.dumps(share).encode())
    info = I.ingest("https://grok.com/share/abc_DEF-1", "x", "T", [], tmp_path, raw_dir=tmp_path / "raw")
    assert seen == ["abc_DEF-1"] and info["source_url"] == "https://grok.com/share/abc_DEF-1"
    assert (tmp_path / "raw/share.json").exists()


# ---- grokbot-chat source type
CHAT = [
    {"clock_utc_minute": "10:24 AM", "speaker": "Eve", "text": "Earlier reply."},
    {"clock_utc_minute": "10:34 AM", "id": "t10u", "speaker": "David", "text": "Two jobs, in order."},
    {"clock_utc_minute": "10:34 AM", "speaker": "Eve", "text": "On it.\n\nSecond paragraph."},
    {"clock_utc_minute": "12:05 PM", "id": "t11u", "speaker": "David", "text": "[REDACTED xAI API key]"},
]


def test_grokbot_chat_render_and_parse(tmp_path):
    import align_audio
    p = tmp_path / "chat.json"
    p.write_text(json.dumps(CHAT))
    info = I.ingest_grokbot_chat(p, "the-making-of-eve", "The Making of Eve", [], tmp_path, "2026-10-23", episode=1)
    md = Path(info["path"]).read_text()
    assert info["stem"] == "2026-10-23_the-making-of-eve" and info["turns"] == 4 and info["source_type"] == "grokbot-chat"
    assert "\nepisode: 1\n" in md and "\nsource: grokbot-chat\n" in md
    assert "[10:34:00] Eve: On it.\n\nSecond paragraph." in md and "[12:05:00] David: [REDACTED xAI API key]" in md
    turns = align_audio.parse_transcript(md)
    assert [t.speaker for t in turns] == ["Eve", "David", "Eve", "David"]
    assert turns[2].text == "On it.\nSecond paragraph." and turns[3].created_at == "2026-10-23T12:05:00.000Z"
    # idempotent, and refuses to overwrite different content
    assert I.ingest_grokbot_chat(p, "the-making-of-eve", "The Making of Eve", [], tmp_path, "2026-10-23", episode=1)["sha256"] == info["sha256"]
    with pytest.raises(I.IngestError):
        I.ingest_grokbot_chat(p, "the-making-of-eve", "Other", [], tmp_path, "2026-10-23")


@pytest.mark.parametrize("bad", [
    [],
    [{"clock_utc_minute": "10:34 AM", "speaker": "Narrator", "text": "x"}],
    [{"clock_utc_minute": "25:00 AM", "speaker": "David", "text": "x"}],
    [{"clock_utc_minute": "10:34", "speaker": "David", "text": "x"}],
    [{"clock_utc_minute": "10:35 AM", "speaker": "David", "text": "a"}, {"clock_utc_minute": "10:34 AM", "speaker": "Eve", "text": "b"}],
    [{"clock_utc_minute": "10:34 AM", "speaker": "David", "text": "key xai-" + "A" * 40}],
])
def test_grokbot_chat_rejects(tmp_path, bad):
    with pytest.raises(I.IngestError):
        I.render_grokbot_chat(bad, "2026-10-23", "t", [])


def test_grokbot_chat_cli(tmp_path, capsys):
    p = tmp_path / "chat.json"
    p.write_text(json.dumps(CHAT))
    assert I.main([str(p), "--source-type", "grokbot-chat", "--slug", "x", "--title", "T", "--out", str(tmp_path)]) == 2
    assert I.main([str(p), "--source-type", "grokbot-chat", "--slug", "x", "--title", "T", "--date", "2026-10-23",
                              "--out", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["turns"] == 4
