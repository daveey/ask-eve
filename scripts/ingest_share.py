#!/usr/bin/env python3
"""Grok share link (or saved share JSON) -> verbatim episodes/<date>_<slug>.md.

Second source type, --source-type grokbot-chat: a JSON list of chat turns extracted from a Grok Bot
conversation ([{"speaker": "David"|"Eve", "text": ..., "clock_utc_minute": "10:34 AM", "id"?: ...}]),
minute precision, rendered as [HH:MM:00] with source: grokbot-chat. Order is the list order.

Read-only: GET https://grok.com/rest/app-chat/share_links/<share_id>. Prints a JSON summary
(path, sha256, bytes, turns, session_started_at, session_ended_at) consumed by d1_upsert.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

SHARE_URL_RE = re.compile(r"^https://grok\.com/share/([A-Za-z0-9_-]+)/?$")
SLUG_RE = re.compile(r"^[a-z0-9-]{1,60}$")
API = "https://grok.com/rest/app-chat/share_links/{}"
SPEAKERS = {"human": "David", "assistant": "Eve"}


class IngestError(Exception):
    pass


def share_id_from_url(url: str) -> str:
    m = SHARE_URL_RE.match(url.strip())
    if not m:
        raise IngestError(f"not a grok.com share URL: {url!r}")
    return m.group(1)


def fetch_share(share_id: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(API.format(share_id), headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https host)
        return r.read()


def sorted_turns(share: dict) -> list[dict]:
    responses = share.get("responses")
    if not isinstance(responses, list) or not responses:
        raise IngestError("share JSON has no responses")
    turns = sorted(responses, key=lambda x: x["createTime"])
    for t in turns:
        if t.get("sender") not in SPEAKERS:
            raise IngestError(f"unexpected sender {t.get('sender')!r} in response {t.get('responseId')}")
    return turns


def render(share: dict, share_id: str, title: str, people: list[str]) -> str:
    turns = sorted_turns(share)
    first, last = turns[0]["createTime"], turns[-1]["createTime"]
    body = "\n\n".join(f"[{t['createTime'][11:19]}] {SPEAKERS[t['sender']]}: {(t.get('message') or '').strip()}" for t in turns) + "\n"
    fm = (
        "---\n"
        f"date: {first[:10]}\n"
        f"title: {title}\n"
        f"people: [{', '.join(people)}]\n"
        f"source: https://grok.com/share/{share_id}\n"
        f"note: Verbatim export of the shared Grok voice session ({len(turns)} turns, {first[11:16]}-{last[11:16]} UTC). "
        "Human turns labeled David, assistant turns labeled Eve. Text unedited, ASR artifacts kept.\n"
        "---\n"
    )
    return fm + "\n" + body


def suggest_people(text: str, people_index: Path) -> list[str]:
    """Names from the index/people.md table that appear with word boundaries (for confirmation only)."""
    if not people_index.exists():
        return []
    names = []
    for line in people_index.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells and cells[0] and not set(cells[0]) <= set("-: ") and cells[0].lower() not in ("name", "person"):
            names.append(cells[0])
    return [n for n in names if re.search(rf"\b{re.escape(n)}\b", text)]


def ingest(source: str, slug: str, title: str, people: list[str] | None, out_dir: Path,
           raw_dir: Path | None = None, force: bool = False, people_index: Path | None = None,
           share_id: str | None = None) -> dict:
    if not SLUG_RE.match(slug):
        raise IngestError(f"bad slug {slug!r}")
    if source.startswith("https://"):
        share_id = share_id_from_url(source)
        raw = fetch_share(share_id)
    else:
        raw = Path(source).read_bytes()
        if not share_id or not re.match(r"^[A-Za-z0-9_-]+$", share_id):
            raise IngestError("saved share JSON: pass --share-id (the id is not stored in the JSON)")
    share = json.loads(raw)
    if people is None:
        text = " ".join((t.get("message") or "") for t in share.get("responses", []))
        people = suggest_people(text, people_index or Path("index/people.md"))
        print(f"suggested people (confirm, then pass --people): {people}", file=sys.stderr)
    doc = render(share, share_id, title, people)
    turns = sorted_turns(share)
    stem = f"{turns[0]['createTime'][:10]}_{slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.md"
    data = doc.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha and not force:
        raise IngestError(f"{path} exists with different content; use --force")
    path.write_bytes(data)
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "share.json").write_bytes(raw)
    return {
        "path": str(path), "stem": stem, "sha256": sha, "bytes": len(data), "turns": len(turns),
        "session_started_at": turns[0]["createTime"], "session_ended_at": turns[-1]["createTime"],
        "source_url": f"https://grok.com/share/{share_id}",
    }


CHAT_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2}) ?([AP]M)$")
SECRET_RE = re.compile(r"xai-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")


def chat_clock(s: str) -> str:
    m = CHAT_CLOCK_RE.match(s.strip())
    if not m:
        raise IngestError(f"bad clock_utc_minute {s!r} (want e.g. '10:34 AM')")
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= h <= 12 and mi < 60):
        raise IngestError(f"bad clock_utc_minute {s!r}")
    return f"{(h % 12) + (12 if ap == 'PM' else 0):02d}:{mi:02d}:00"


def render_grokbot_chat(chat: list, date: str, title: str, people: list[str], episode: int | None = None,
                        note: str | None = None) -> tuple[str, list[dict]]:
    if not isinstance(chat, list) or not chat:
        raise IngestError("grokbot-chat JSON must be a non-empty list of turns")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise IngestError(f"bad date {date!r}")
    turns, prev = [], None
    for k, t in enumerate(chat):
        if not isinstance(t, dict) or t.get("speaker") not in ("David", "Eve") or not isinstance(t.get("text"), str):
            raise IngestError(f"turn {k}: need speaker David|Eve and text")
        if SECRET_RE.search(t["text"]):
            raise IngestError(f"turn {k}: looks like it contains a credential; redact it first")
        clock = chat_clock(t.get("clock_utc_minute", ""))
        if prev and clock < prev:
            raise IngestError(f"turn {k}: clock goes backwards ({clock} < {prev}); midnight sessions are not supported")
        prev = clock
        turns.append({"createTime": f"{date}T{clock}.000Z", "speaker": t["speaker"], "message": t["text"], "chat_id": t.get("id")})
    body = "\n\n".join(f"[{t['createTime'][11:19]}] {t['speaker']}: {t['message'].strip()}" for t in turns) + "\n"
    fm = ["---", f"date: {date}", f"title: {title}"]
    if episode is not None:
        fm.append(f"episode: {episode}")
    fm += [f"people: [{', '.join(people)}]", "source: grokbot-chat",
           f"note: {note or f'Grok Bot chat turns ({len(turns)} turns, {turns[0]["createTime"][11:16]}-{turns[-1]["createTime"][11:16]} UTC), minute-precision clocks. Text unedited.'}",
           "---"]
    return "\n".join(fm) + "\n\n" + body, turns


def ingest_grokbot_chat(source: Path, slug: str, title: str, people: list[str] | None, out_dir: Path, date: str,
                        episode: int | None = None, note: str | None = None, force: bool = False) -> dict:
    if not SLUG_RE.match(slug):
        raise IngestError(f"bad slug {slug!r}")
    doc, turns = render_grokbot_chat(json.loads(Path(source).read_bytes()), date, title, people or [], episode, note)
    stem = f"{date}_{slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.md"
    data = doc.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha and not force:
        raise IngestError(f"{path} exists with different content; use --force")
    path.write_bytes(data)
    return {"path": str(path), "stem": stem, "sha256": sha, "bytes": len(data), "turns": len(turns),
            "session_started_at": turns[0]["createTime"], "session_ended_at": turns[-1]["createTime"], "source_url": None,
            "source_type": "grokbot-chat"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="https://grok.com/share/<id> or path to a saved share JSON")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--people", help="comma-separated; if omitted, suggestions are printed and the list is left empty")
    ap.add_argument("--share-id", help="required when SOURCE is a saved JSON file")
    ap.add_argument("--out", default="episodes/", type=Path)
    ap.add_argument("--raw-dir", type=Path)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--source-type", choices=["grok-share", "grokbot-chat"], default="grok-share")
    ap.add_argument("--date", help="grokbot-chat: session date YYYY-MM-DD (UTC)")
    ap.add_argument("--episode", type=int, help="grokbot-chat: episode number for the front matter")
    ap.add_argument("--note", help="grokbot-chat: front-matter note")
    a = ap.parse_args(argv)
    people = [p.strip() for p in a.people.split(",") if p.strip()] if a.people else None
    try:
        if a.source_type == "grokbot-chat":
            if not a.date:
                raise IngestError("--date is required for grokbot-chat")
            print(json.dumps(ingest_grokbot_chat(Path(a.source), a.slug, a.title, people, a.out, a.date, a.episode, a.note, a.force), indent=2))
            return 0
        info = ingest(a.source, a.slug, a.title, people, a.out, a.raw_dir, a.force, share_id=a.share_id)
    except IngestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
