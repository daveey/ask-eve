import json
import subprocess
import wave

import pytest

from pipeline_helpers import FakeTTS, rms, write_tone_wav
import revoice_eve as R
import align_audio as A

DAVID = [(1.0, 3.0), (6.0, 8.0), (8.8, 10.0)]


def alignment():
    # David 0 [1,3]; Eve 1 slot [3.3,5.7] (2.4 s); David 2 [6,8]; Eve 3 slot [8.3,8.5] (0.2 s); David 4 [8.8,10]
    return {"turns": [
        {"i": 0, "speaker": "David", "raw_start": 1.0, "raw_end": 3.0, "method": "asr-match", "score": 99.0},
        {"i": 1, "speaker": "Eve", "raw_start": 3.3, "raw_end": 5.7, "method": "gap-placed", "score": None},
        {"i": 2, "speaker": "David", "raw_start": 6.0, "raw_end": 8.0, "method": "asr-match", "score": 97.0},
        {"i": 3, "speaker": "Eve", "raw_start": 8.3, "raw_end": 8.5, "method": "gap-placed", "score": None},
        {"i": 4, "speaker": "David", "raw_start": 8.8, "raw_end": 10.0, "method": "interpolated", "score": None},
    ]}


@pytest.fixture
def david(tmp_path):
    p = tmp_path / "david.wav"
    write_tone_wav(p, 12.0, [(s, e, 1000) for s, e in DAVID])
    return p


def test_timemap_insert_and_cut():
    tm = R.build_timemap([(2.0, "insert", 1.5), (5.0, "cut", 7.0)], 10.0)
    assert tm.to_edited(1.0) == 1.0
    assert tm.to_edited(2.0, side="left") == 2.0 and tm.to_edited(2.0) == 3.5
    assert tm.to_edited(4.0) == 5.5
    assert tm.to_edited(6.0) == pytest.approx(6.5)          # inside the cut -> cut point
    assert tm.to_edited(8.0) == pytest.approx(7.5)
    assert tm.as_json()[-1] == {"raw_start": 7.0, "raw_end": 10.0, "edited_start": 6.5}


def test_split_text_limit():
    parts = R.split_text("A sentence. " * 20, limit=50)
    assert all(len(p) <= 50 for p in parts) and " ".join(parts).split() == ("A sentence. " * 20).split()


def test_placement_insertion_and_time_map(tmp_path, david):
    tts = FakeTTS({"short gap": 1.5, "long gap": 1.0})
    text = {1: "Eve in the long gap.", 3: "Eve in the short gap."}
    r = R.revoice(alignment(), text, david, tmp_path / "o", tts, pause_after=0.35)
    by = {t["i"]: t for t in r["turns"]}
    # clip 1 (1.0 s) fits its 2.4 s slot: no shift, natural pause kept
    assert by[1]["edited_start"] == pytest.approx(3.3) and by[1]["edited_end"] == pytest.approx(4.3, abs=0.01)
    assert by[2]["edited_start"] == pytest.approx(6.0)
    # clip 3 (1.5 s) > 0.2 s slot: insert 1.5 + 0.35 - 0.2 = 1.65 s at the slot start
    assert r["inserted_seconds"] == pytest.approx(1.65, abs=0.01)
    assert by[3]["edited_start"] == pytest.approx(8.3)
    assert by[4]["edited_start"] == pytest.approx(8.8 + 1.65, abs=0.01)
    assert r["time_map"] == [{"raw_start": 0.0, "raw_end": 8.3, "edited_start": 0.0},
                             {"raw_start": 8.3, "raw_end": 12.0, "edited_start": pytest.approx(9.95, abs=0.01)}]
    assert r["edited_duration"] == pytest.approx(12.0 + 1.65, abs=0.02)
    d, e, m = (tmp_path / "o" / f for f in ("david_edit.wav", "eve_track.wav", "mix.wav"))
    # David's last turn moved later; the inserted span is silent on David's track
    assert rms(d, 8.4, 9.8) < 0.001 and rms(d, 10.6, 11.5) > 0.1 and rms(d, 8.85, 9.9) < 0.001
    # Eve is where the timeline says, and never overlaps David
    assert rms(e, 3.4, 4.2) > 0.05 and rms(e, 8.4, 9.7) > 0.05 and rms(e, 6.1, 7.9) < 0.001
    for k in range(0, 136):
        s = k * 0.1
        assert not (rms(d, s, s + 0.1) > 0.03 and rms(e, s, s + 0.1) > 0.03), f"overlap at {s:.1f}s"
    assert rms(m, 3.4, 4.2) > 0.05 and rms(m, 10.6, 11.5) > 0.1
    with wave.open(str(m)) as w:
        assert w.getframerate() == 48000 and w.getnchannels() == 1
    assert json.loads((tmp_path / "o/revoice.json").read_text())["recording_method"] == "mic-plus-tts"


def test_shorter_clip_pads_and_max_pause_cuts(tmp_path, david):
    tts = FakeTTS({}, default=0.5)
    text = {1: "Eve one.", 3: "Eve three."}
    r = R.revoice(alignment(), text, david, tmp_path / "o", tts, pause_after=0.35, max_pause=0.8)
    by = {t["i"]: t for t in r["turns"]}
    # slot 1 is 2.4 s, clip 0.5 s: surplus cut to 0.8 s pause (David 0 and 2 are asr-matched)
    assert r["cut_seconds"] == pytest.approx(5.7 - (3.3 + 0.5 + 0.8), abs=0.01)
    assert by[2]["edited_start"] == pytest.approx(6.0 - r["cut_seconds"], abs=0.01)
    # slot 3 neighbours an interpolated David turn: not cut, but needs insertion (0.85 > 0.2)
    assert r["inserted_seconds"] == pytest.approx(0.65, abs=0.01)


def test_clip_cache_avoids_rebilling(tmp_path):
    tts = FakeTTS({}, default=0.3)
    a = R.make_clip(tts, "Hello there.", tmp_path)
    b = R.make_clip(tts, "Hello there.", tmp_path)
    assert a == b and len(tts.calls) == 1


def test_synthetic_episode_with_disclaimer_and_two_voices(tmp_path):
    md = "---\ndate: 2026-11-01\ntitle: t\npeople: []\n---\n\n[10:00:00] David: Question one?\n\n[10:00:05] Eve: Answer one.\n\n[10:00:09] Eve: \n"
    turns = A.parse_transcript(md)
    eve, nar = FakeTTS({}, default=0.8), FakeTTS({}, default=0.6)
    eve.name, nar.name = "fake eve", "fake narrator"
    r = R.synthesize_episode(turns, eve, nar, tmp_path / "s", tmp_path / "cache", pause=0.5)
    assert nar.calls[0] == R.DISCLAIMER and nar.calls[1] == "Question one?" and eve.calls == ["Answer one."]
    assert r["recording_method"] == "tts-reconstructed" and r["placeholder_audio"] is True
    assert r["missing_turns"] == [2]
    t0, t1 = r["turns"][0], r["turns"][1]
    assert r["disclaimer"]["start"] == 0.5 and t0["edited_start"] == pytest.approx(0.5 + 0.6 + 1.0, abs=0.01)
    assert t1["edited_start"] == pytest.approx(t0["edited_end"] + 0.5, abs=0.01)
    assert rms(tmp_path / "s/mix.wav", t1["edited_start"] + 0.1, t1["edited_end"] - 0.1) > 0.03


def test_failed_real_clip_becomes_placeholder(tmp_path):
    class Flaky(FakeTTS):
        placeholder = False
        def synthesize(self, text):
            if "boom" in text:
                raise RuntimeError("xAI TTS HTTP 500: oops")
            return super().synthesize(text)
    md = "---\ndate: 2026-11-01\ntitle: t\npeople: []\n---\n\n[10:00:00] David: fine\n\n[10:00:05] Eve: boom\n"
    eve, nar = Flaky({}, default=0.4), Flaky({}, default=0.4)
    eve.name, nar.name, eve.voice, nar.voice = "e", "n", "eve", "rex"
    r = R.synthesize_episode(A.parse_transcript(md), eve, nar, tmp_path / "s", tmp_path / "c")
    assert r["placeholder_audio"] is True and r["placeholder_turns"] == [1] and "500" in r["tts_failures"]["1"]


def test_make_client_voices(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("TTS_BACKEND", raising=False)
    monkeypatch.delenv("EVE_VOICE", raising=False)
    monkeypatch.delenv("NARRATOR_VOICE", raising=False)
    assert R.make_client("eve").name == "placeholder-silent voice=eve"
    assert R.make_client("narrator").name == "placeholder-silent voice=rex"
    monkeypatch.setenv("NARRATOR_VOICE", "EVE")
    with pytest.raises(ValueError):
        R.make_client("narrator")
    monkeypatch.setenv("NARRATOR_VOICE", "leo")
    monkeypatch.setenv("XAI_API_KEY", "test-not-a-key")
    c = R.make_client("narrator")
    assert isinstance(c, R.XAITTS) and c.payload("hi") == {
        "text": "hi", "voice_id": "leo", "language": "en", "output_format": {"codec": "wav", "sample_rate": 48000}}


def test_xai_client_retries_then_raises(monkeypatch):
    import io
    import urllib.error
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req.get_header("Authorization"))
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "rate", {}, io.BytesIO(b"slow down"))
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"bad text"))
    monkeypatch.setattr(R.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    c = R.XAITTS("test-key", retries=5)
    with pytest.raises(RuntimeError, match="HTTP 400: bad text"):
        c.synthesize("x")
    assert calls == ["Bearer test-key"] * 3
