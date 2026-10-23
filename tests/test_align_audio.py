import json
import random

import pytest

from pipeline_helpers import EP1, SHARE_JSON
import align_audio as A

MD = """---
date: 2026-11-01
title: t
people: []
---

[10:00:00] David: Ask Eve take one. So tell me what happened with the venture capital firms this year.

[10:00:20] Eve: They moved right, mostly because of regulation and money.

[10:01:00] David: Okay but what about the crypto lobbying money and the super PAC spending in the midterms?

[10:01:30] Eve: That was a big part of it.
Second paragraph of the same turn.

[10:02:10] Eve: And one more thing on that.

[10:02:40] David: Yes.

[10:02:50] Eve: Good.

[10:03:10] David: Let us switch to housing policy in San Francisco and the zoning fights in Sacramento now.

[10:03:40] Eve: Sure.
"""


def words_for(turns, offset, drop=(), corrupt=0.0, rate=0.35, seed=0):
    rnd = random.Random(seed)
    out, cur = [], 0.0
    truth = {}
    for t in turns:
        if t.speaker != "David":
            continue
        s = max(t.t + offset - len(t.text.split()) * 0.0, cur + 0.5)
        truth[t.index] = s
        c = s
        for w in t.text.split():
            if t.index not in drop:
                out.append({"w": "zzz" if rnd.random() < corrupt else w, "start": round(c, 3), "end": round(c + rate * 0.8, 3)})
            c += rate
        truth[(t.index, "end")] = c - rate * 0.2
        cur = c
    return out, truth


def test_parse_multiparagraph_and_times():
    turns = A.parse_transcript(MD)
    assert [t.speaker for t in turns] == ["David", "Eve", "David", "Eve", "Eve", "David", "Eve", "David", "Eve"]
    assert turns[3].text == "That was a big part of it.\nSecond paragraph of the same turn."
    assert turns[2].t == 60 and turns[0].created_at == "2026-11-01T10:00:00.000Z"


def test_midnight_rollover():
    md = "---\ndate: 2026-11-01\n---\n\n[23:59:50] David: a\n\n[00:00:10] Eve: b\n"
    turns = A.parse_transcript(md)
    assert turns[1].t == 20 and turns[1].created_at.startswith("2026-11-02T00:00:10")


def test_david_matched_eve_gap_placed():
    turns = A.parse_transcript(MD)
    words, truth = words_for(turns, offset=7.0)
    r = A.align(turns, [A.Word(w["w"], w["start"], w["end"]) for w in words])
    by = {t["i"]: t for t in r["turns"]}
    assert by[0]["method"] == "sync-phrase" and abs(by[0]["raw_start"] - truth[0]) < 0.01
    assert r["sync_offset_seconds"] == pytest.approx(truth[0], abs=0.01)
    for i in (2, 7):
        assert by[i]["method"] == "asr-match" and abs(by[i]["raw_start"] - truth[i]) < 0.01
    assert by[5]["method"] == "interpolated"          # "Yes." is too short to match
    # Eve turn 1 sits in the gap: after David 0 ends, before David 2 starts
    assert by[1]["method"] == "gap-placed"
    assert by[1]["raw_start"] == pytest.approx(by[0]["raw_end"] + 0.3, abs=1e-3)
    assert by[1]["raw_end"] == pytest.approx(by[2]["raw_start"] - 0.3, abs=1e-3)
    # consecutive Eve turns 3,4 split their gap by createTime deltas (40 s vs 30 s)
    g0, g1 = by[3]["raw_start"], by[4]["raw_end"]
    assert by[3]["raw_end"] == pytest.approx(by[4]["raw_start"])
    assert (by[3]["raw_end"] - g0) / (g1 - g0) == pytest.approx(40 / 70, abs=1e-3)
    assert r["alignment_coverage"] == 1.0 and r["alignment_method"] == "asr-david-gaps"
    assert all(t["raw_end"] >= t["raw_start"] for t in r["turns"])


def test_missing_david_turn_is_interpolated_and_monotonic():
    turns = A.parse_transcript(MD)
    words, truth = words_for(turns, offset=3.0, drop={2})
    r = A.align(turns, [A.Word(w["w"], w["start"], w["end"]) for w in words])
    by = {t["i"]: t for t in r["turns"]}
    assert by[2]["method"] == "interpolated"
    assert by[0]["raw_end"] <= by[2]["raw_start"] <= by[7]["raw_start"]
    assert r["alignment_coverage"] == pytest.approx(2 / 3, abs=1e-3)
    starts = [t["raw_start"] for t in r["turns"]]
    assert starts == sorted(starts)


def test_no_words_falls_back_to_creationdate_prior():
    turns = A.parse_transcript(MD)
    r = A.align(turns, [], anchor_offset=12.0)
    assert r["alignment_method"] == "creationdate-only" and r["alignment_coverage"] == 0
    assert r["turns"][2]["raw_start"] == pytest.approx(72.0)


def test_cli_with_json_words_and_min_coverage(tmp_path, capsys):
    turns = A.parse_transcript(MD)
    words, _ = words_for(turns, offset=5.0, drop={0, 2, 7})
    (tmp_path / "md.md").write_text(MD)
    (tmp_path / "w.json").write_text(json.dumps(words))
    rc = A.main(["--transcript", str(tmp_path / "md.md"), "--asr-words", str(tmp_path / "w.json"), "--out", str(tmp_path / "a.json")])
    assert rc == 3          # coverage 0 < 0.5
    rc = A.main(["--transcript", str(tmp_path / "md.md"), "--asr-words", str(tmp_path / "w.json"), "--out", str(tmp_path / "a.json"),
                 "--allow-low-coverage"])
    assert rc == 0 and json.loads((tmp_path / "a.json").read_text())["asr_model"] == "json-words"


def test_both_speakers_mode_matches_eve():
    turns = A.parse_transcript(MD)
    words, cur = [], 0.0
    for t in turns:
        for w in t.text.split():
            words.append(A.Word(w, cur, cur + 0.3)); cur += 0.35
        cur += 1.0
    r = A.align(turns, words, match_speakers=("David", "Eve"), sync_phrase=None)
    assert {t["method"] for t in r["turns"] if t["speaker"] == "Eve" and len(t.get("method", "")) and t["i"] in (1, 3)} == {"asr-match"}


@pytest.mark.skipif(not SHARE_JSON.exists(), reason="episode-1 share JSON not on this machine")
def test_episode1_scale_synthetic_words():
    turns = A.parse_transcript(EP1.read_text(), json.loads(SHARE_JSON.read_text()))
    assert len(turns) == 304
    rnd = random.Random(1)
    words, cur = [], 0.0
    for t in turns:
        if t.speaker != "David":
            continue
        s = max(t.t + 12.0, cur + 1.0)
        for w in t.text.split():
            words.append(A.Word("zzq" if rnd.random() < 0.08 else w, s, s + 0.3)); s += 0.38
        cur = s
    r = A.align(turns, words)
    assert r["alignment_coverage"] > 0.9
    assert all(a["raw_start"] <= b["raw_start"] for a, b in zip(r["turns"], r["turns"][1:]))


def test_parse_transcript_keeps_relayed_as_metadata_out_of_spoken_text():
    from align_audio import parse_transcript
    md = ("---\ndate: 2026-10-23\ntitle: x\n---\n\n"
          "[10:34:40] David: Okay, well do that.\n"
          "> relayed as (written prompt, 10:34, not spoken audio):\n"
          "> Two jobs, in order.\n"
          ">\n"
          "> (1) Update the doc.\n"
          "and then build it.\n\n"
          "[10:34:41] Eve: On it.\n")
    turns = parse_transcript(md)
    assert [t.speaker for t in turns] == ["David", "Eve"]
    assert turns[0].text == "Okay, well do that.\nand then build it."
    assert "Two jobs" not in turns[0].text and "relayed as" not in turns[0].text
    assert turns[0].meta.startswith("relayed as") and "(1) Update the doc." in turns[0].meta
    assert turns[1].text == "On it." and turns[1].meta == ""


def test_short_turn_span_does_not_swallow_next_turn_after_pause():
    # episode 2 regression: ASR dropped fillers ("uh", "all, all"), so n-word extension ran into David's NEXT turn
    md = ("---\ndate: 2026-11-01\ntitle: t\npeople: []\n---\n\n"
          "[10:00:00] David: Well, uh, what were we talking about on our last podcast?\n\n[10:00:05] Eve: Setup notes.\n\n"
          "[10:00:30] David: Do you not have it?\n\n[10:00:35] Eve: No.\n\n"
          "[10:01:00] David: Do you have access to my list of sessions?\n")
    def seq(text, t0):
        out = []
        for k, w in enumerate(text.split()):
            out.append(A.Word(w, t0 + 0.3 * k, t0 + 0.3 * k + 0.25))
        return out
    words = (seq("Well, what were we talking about on our last podcast?", 2.0) + seq("Do you not have it?", 30.0)
             + seq("Do you have access to my list of sections?", 60.0))
    r = A.align(A.parse_transcript(md), words)
    by = {t["i"]: t for t in r["turns"]}
    assert by[0]["raw_end"] < 10 and by[1]["raw_end"] > by[1]["raw_start"]     # Eve's slot is real, not zero-length
    assert by[2]["method"] == "asr-match" and by[2]["raw_start"] == pytest.approx(30.0)
    assert by[4]["method"] == "asr-match" and by[4]["raw_start"] == pytest.approx(60.0)
