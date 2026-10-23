#!/usr/bin/env python3
"""Find the moments where each person in index/people.md is substantively discussed in an episode.

A *moment* is a contiguous span of turns where the person is the subject: David's question about them plus Eve's
answer(s), or a meaningful claim, fact or take about them. Passing mentions, name lists, asides and name-drops do not
count. An LLM (xAI API, temperature 0) judges each person separately over the whole numbered transcript, then
adjacent spans about the same person are merged (and re-summarized). Timestamps come from the episode timeline.

    python scripts/extract_moments.py episodes/<stem>.md [--timeline artifacts/<stem>/timeline.json] [--out index/moments/<slug>.json]

Output (committed, reviewable): index/moments/<episode-slug>.json. Needs XAI_API_KEY in the environment (never logged).
Stdlib only, so it runs on a bare GitHub runner.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frontmatter import parse_front_matter_block  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODEL = os.environ.get("MOMENTS_MODEL", "grok-4.7")
PROMPT_VERSION = 1
WORKERS = int(os.environ.get("MOMENTS_WORKERS", "6"))
API = "https://api.x.ai/v1/chat/completions"
TURN_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] (David|Eve): ?(.*)$")

SYSTEM = """You index a podcast transcript. The show is a conversation between David (the host, a human) and Eve (an AI co-host).
You are given the full transcript as numbered turns, and ONE person to look for. Find the segments where that person is
SUBSTANTIVELY discussed.

A segment is a contiguous span of turns (start_turn..end_turn, inclusive) where the person is a main subject:
- a question from David about the person plus Eve's answer(s) and the follow-ups that stay on that person, or
- a meaningful claim, fact, story, analysis or opinion about the person.
Do NOT count: passing mentions, lists of names, asides, name-drops, a person used only as a comparison while the
discussion is about someone else, meta talk about the show's people index or files, or turns where the name appears
only in a sidebar list or a tag. The person must actually be talked ABOUT.
Transcripts come from speech recognition: names may be misspelled (aliases are given). Use the disambiguation notes.

Each segment needs:
- start_turn, end_turn: turn numbers from the transcript. Keep spans tight around the discussion of the person; one
  question+answer exchange is usually 2 turns. Merge back-to-back exchanges about the same person into one segment.
- summary: 1-2 sentences, faithful to the transcript, no outside facts, in the form "David asks ...; Eve argues/explains ..."
  (or "Eve explains ..." when David did not ask). Present tense. Say what Eve actually says about the person, concretely.
  Report claims as Eve's claims, not as facts. At most 50 words.
- topics: 1-4 short lowercase kebab-case tags for what is discussed (e.g. "ai-policy", "usaid", "spacs", "succession").

Return JSON: {"segments": [...]} - an empty list if the person is never substantively discussed."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["segments"], "properties": {"segments": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["start_turn", "end_turn", "summary", "topics"],
                               "properties": {"start_turn": {"type": "integer"}, "end_turn": {"type": "integer"},
                                              "summary": {"type": "string"},
                                              "topics": {"type": "array", "items": {"type": "string"}}}}}}}

RESUMMARIZE = """You summarize one segment of a podcast transcript about one person. David is the human host, Eve the
AI co-host. Write 1-2 sentences, faithful to the transcript, no outside facts, in the form
"David asks ...; Eve argues/explains ..." (or "Eve explains ..." when David did not ask). Present tense. Report
claims as Eve's claims. At most 50 words. Also give 1-4 lowercase kebab-case topic tags. Return JSON {"summary": "...", "topics": [...]}."""


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse(md: str) -> tuple[dict, list[dict]]:
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    fm = parse_front_matter_block(m.group(1)) if m else {}
    turns: list[dict] = []
    for line in (md[m.end():] if m else md).split("\n"):
        t = TURN_RE.match(line)
        if t:
            turns.append({"i": len(turns), "speaker": t.group(4), "clock": f"{t.group(1)}:{t.group(2)}:{t.group(3)}",
                          "lines": [t.group(5)]})
        elif turns and not line.startswith(">"):
            turns[-1]["lines"].append(line)
    for t in turns:
        t["text"] = "\n".join(t.pop("lines")).strip()
    return fm, turns


def load_people() -> list[dict]:
    aliases = json.loads((ROOT / "index/people-aliases.json").read_text())
    out = []
    for line in (ROOT / "index/people.md").read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 2 and cells[0] not in ("Person", "") and not set(cells[0]) <= set("-: "):
            slug = slugify(cells[0])
            a = aliases.get(slug, {})
            out.append({"slug": slug, "name": a.get("name", cells[0]), "aliases": a.get("aliases", []),
                        "notes": a.get("notes", "")})
    return out


def numbered(turns: list[dict]) -> str:
    return "\n\n".join(f"[T{t['i']}] {t['speaker']} ({t['clock']}): {t['text']}" for t in turns)


def chat(messages: list[dict], schema: dict | None, key: str, retries: int = 4) -> dict:
    body = {"model": MODEL, "temperature": 0, "messages": messages}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": schema, "strict": True}}
    else:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for k in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.load(r)
            return json.loads(data["choices"][0]["message"]["content"])
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            msg = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            print(f"  xAI call failed ({msg}); retry {k + 1}/{retries}", file=sys.stderr)
            time.sleep(5 * (k + 1))
    raise SystemExit("xAI API failed repeatedly")


def merge(segs: list[dict], gap: int = 1) -> list[list[dict]]:
    """Group segments whose spans overlap or are adjacent (next start <= prev end + gap)."""
    groups: list[list[dict]] = []
    for s in sorted(segs, key=lambda s: (s["start_turn"], s["end_turn"])):
        if groups and s["start_turn"] <= max(x["end_turn"] for x in groups[-1]) + gap:
            groups[-1].append(s)
        else:
            groups.append([s])
    return groups


def tag(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def person_moments(p: dict, turns: list[dict], text: str, offs: dict, ep_slug: str, key: str) -> list[dict]:
    who = (f"PERSON: {p['name']}\nAliases / ASR spellings: {', '.join(p['aliases']) or '(none)'}\n"
           f"Disambiguation: {p['notes'] or '(none)'}")
    out = chat([{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"{who}\n\nTRANSCRIPT ({len(turns)} turns, T0..T{len(turns) - 1}):\n\n{text}\n\n{who}"}],
               SCHEMA, key)
    segs = [s for s in out["segments"] if 0 <= s["start_turn"] <= s["end_turn"] < len(turns)]
    moments = []
    for g in merge(segs):
        a, b = min(s["start_turn"] for s in g), max(s["end_turn"] for s in g)
        if len(g) == 1:
            summary, topics = g[0]["summary"], g[0]["topics"]
        else:   # merged span: summarize the whole span again so it stays 1-2 sentences
            r = chat([{"role": "system", "content": RESUMMARIZE},
                      {"role": "user", "content": f"{who}\n\nSEGMENT (T{a}..T{b}):\n\n{numbered(turns[a:b + 1])}"}], None, key)
            summary, topics = r["summary"], r.get("topics") or [t for s in g for t in s["topics"]]
        moments.append({"person": p["slug"], "name": p["name"], "episode": ep_slug, "start_turn": a, "end_turn": b,
                        "start": offs.get(a, (None, None))[0], "end": offs.get(b, (None, None))[1],
                        "clock": turns[a]["clock"], "summary": summary.strip(),
                        "topics": list(dict.fromkeys(tag(t) for t in topics if tag(t)))[:4]})
    print(f"  {p['slug']}: {len(moments)} moment(s)", file=sys.stderr)
    return moments


def extract(transcript: Path, timeline: Path | None, key: str, only: set[str] | None = None) -> dict:
    md = transcript.read_text(encoding="utf-8")
    fm, turns = parse(md)
    offs = {}
    if timeline and timeline.exists():
        offs = {t["i"]: (t.get("edited_start"), t.get("edited_end")) for t in json.loads(timeline.read_text())["turns"]}
    text = numbered(turns)
    stem = transcript.stem
    ep_slug = stem.split("_", 1)[1]
    people = [p for p in load_people() if not only or p["slug"] in only]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        per_person = list(pool.map(lambda p: person_moments(p, turns, text, offs, ep_slug, key), people))
    moments = [m for ms in per_person for m in ms]
    moments.sort(key=lambda m: (m["start_turn"], m["person"]))
    return {"episode": ep_slug, "stem": stem, "title": fm.get("title", ""), "turns": len(turns),
            "transcript_sha256": hashlib.sha256(md.encode()).hexdigest(), "model": MODEL, "temperature": 0,
            "prompt_version": PROMPT_VERSION, "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "moments": moments}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--timeline", type=Path, help="default: artifacts/<stem>/timeline.json")
    ap.add_argument("--out", type=Path, help="default: index/moments/<episode-slug>.json")
    ap.add_argument("--person", action="append", help="only these person slugs (merged into an existing file)")
    a = ap.parse_args(argv)
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise SystemExit("XAI_API_KEY is not set")
    stem = a.transcript.stem
    timeline = a.timeline or ROOT / f"artifacts/{stem}/timeline.json"
    out = a.out or ROOT / f"index/moments/{stem.split('_', 1)[1]}.json"
    only = set(a.person) if a.person else None
    res = extract(a.transcript, timeline, key, only)
    if only and out.exists():   # replace just those people's moments
        old = json.loads(out.read_text())
        res["moments"] = sorted([m for m in old["moments"] if m["person"] not in only] + res["moments"],
                                key=lambda m: (m["start_turn"], m["person"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"out": str(out), "moments": len(res["moments"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
