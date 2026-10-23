"""scripts/extract_moments.py: deterministic parts (no API calls) and the committed moments data."""
import json
import re

from pipeline_helpers import ROOT
import build_site as B
import extract_moments as X


def test_turn_numbering_matches_site():
    for md in sorted((ROOT / "episodes").glob("2026-*.md")):
        text = md.read_text()
        assert [(t["i"], t["speaker"]) for t in X.parse(text)[1]] == [(t["i"], t["speaker"]) for t in B.parse_turns(text)]


def test_aliases_cover_people_index_and_asr_spellings():
    people = {p["slug"]: p for p in X.load_people()}
    assert set(people) == {p["slug"] for p in B.load_people_index(ROOT / "index/people.md")}
    assert "Sachs" in people["david-sacks"]["aliases"]
    assert {"DeMuth", "Dimoth", "DeMoth"} <= set(people["chamath-palihapitiya"]["aliases"])
    assert "Madani" in people["zohran-mamdani"]["aliases"]
    assert people["chan"]["name"] == "Connie Chan" and "Connie" in people["chan"]["aliases"]
    assert "Christine" in people["nancy-pelosi"]["notes"]


def test_merge_adjacent_segments():
    seg = lambda a, b: {"start_turn": a, "end_turn": b}   # noqa: E731
    groups = X.merge([seg(10, 11), seg(4, 5), seg(12, 13), seg(6, 6), seg(20, 21)])
    assert [(min(s["start_turn"] for s in g), max(s["end_turn"] for s in g)) for g in groups] == [(4, 6), (10, 13), (20, 21)]


def test_committed_moments_are_well_formed():
    files = sorted((ROOT / "index/moments").glob("*.json"))
    assert files
    slugs = {p["slug"] for p in X.load_people()}
    for f in files:
        d = json.loads(f.read_text())
        md = ROOT / f"episodes/{d['stem']}.md"
        turns = X.parse(md.read_text())[1]
        assert d["turns"] == len(turns) and d["temperature"] == 0
        for m in d["moments"]:
            assert m["person"] in slugs and m["episode"] == f.stem
            assert 0 <= m["start_turn"] <= m["end_turn"] < len(turns)
            assert m["start"] is None or m["end"] is None or m["start"] < m["end"]
            assert m["summary"] and len(m["summary"].split()) <= 90
            assert m["topics"] and all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", t) for t in m["topics"])
        per = {}
        for m in d["moments"]:   # adjacent spans about the same person were merged
            per.setdefault(m["person"], []).append((m["start_turn"], m["end_turn"]))
        for spans in per.values():
            spans.sort()
            assert all(b[0] > a[1] + 1 for a, b in zip(spans, spans[1:]))


def test_no_secret_in_moments_or_script():
    for f in [ROOT / "scripts/extract_moments.py", *(ROOT / "index/moments").glob("*.json")]:
        assert not re.search(r"xai-[A-Za-z0-9]{20,}", f.read_text())
