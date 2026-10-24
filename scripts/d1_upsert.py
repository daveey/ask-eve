#!/usr/bin/env python3
"""Generate idempotent D1/SQLite SQL for one episode. No network: apply the output with
`wrangler d1 execute ask-eve-meta --remote --file <out.sql>` (publish job) or sqlite3 locally.

Inputs: the transcript (front matter: date/title/people/source), optional alignment JSON
(align_audio.py), optional revoice JSON (revoice_eve.py) for turn offsets, and optional
sources JSON (artifacts/<slug>/sources.json) for primary sources, receipts, and spans.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from align_audio import TURN_RE, parse_transcript  # noqa: E402
from frontmatter import episode_type, parse_front_matter_block  # noqa: E402

AUDIO_BASE = "https://audio.askevepodcast.com/"
AI_DISCLOSURE = ("AI-reconstructed audio: the voices in this episode were generated with xAI text-to-speech from the "
                 "verbatim text transcript (Eve in the 'eve' voice, David's lines in a narrator voice). No original audio was used.")
CLONE_AI_DISCLOSURE = ("AI-reconstructed audio: the voices in this episode were generated from the verbatim text transcript "
                       "(Eve with xAI text-to-speech in the 'eve' voice, David's lines with his ElevenLabs AI voice clone). "
                       "No original audio was used.")


def q(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def front_matter(md: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    if not m:
        raise ValueError("transcript has no front matter")
    fm = parse_front_matter_block(m.group(1))
    p = fm.get("people", "[]").strip()
    fm["people"] = [x.strip() for x in p.strip("[]").split(",") if x.strip()]
    return fm


def parse_turn_texts(md: str) -> list[str]:
    """Parse speaker turn texts preserving blank lines between paragraphs (matching build_site.parse_turns)."""
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    body = md[m.end():] if m else md
    turns: list[list[str]] = []
    for line in body.split("\n"):
        tm = TURN_RE.match(line)
        if tm:
            turns.append([tm.group(5)])
        elif turns and line.startswith(">"):
            continue
        elif turns:
            turns[-1].append(line)
    return ["\n".join(lines).strip() for lines in turns]


def normalize_sources_payload(payload: dict | list | None, default_episode_slug: str) -> list[tuple[str, list[dict]]]:
    """Normalize a sources.json payload (either a list of source dicts or {'episode_slug': ..., 'sources': [...]})
    into a list of (episode_slug, sources_list) pairs."""
    if not payload:
        return []
    if isinstance(payload, list):
        return [(default_episode_slug, payload)]
    if isinstance(payload, dict):
        ep_slug = payload.get("episode_slug") or default_episode_slug
        srcs = payload.get("sources")
        if isinstance(srcs, list):
            return [(ep_slug, srcs)]
    raise ValueError("sources payload must be a list of source objects or a dict with a 'sources' list")


def validate_sources(sources_list: list[dict], turn_texts: list) -> None:
    """Validate source metadata and character span offsets against the episode's turns."""
    norm_turns: list[str] = []
    for t in turn_texts:
        if isinstance(t, str):
            norm_turns.append(t)
        elif hasattr(t, "text"):
            norm_turns.append(t.text)
        elif isinstance(t, dict) and "text" in t:
            norm_turns.append(t["text"])
        else:
            raise ValueError(f"unsupported turn object: {type(t)}")

    for idx, s in enumerate(sources_list):
        if not isinstance(s, dict):
            raise ValueError(f"source[{idx}] must be a dict")
        url = (s.get("url") or "").strip()
        title = (s.get("title") or "").strip()
        if not url:
            raise ValueError(f"source[{idx}] is missing a non-empty url")
        if not title:
            raise ValueError(f"source[{idx}] is missing a non-empty title")
        if s.get("turn_index") is not None:
            ti = int(s["turn_index"])
            if not (0 <= ti < len(norm_turns)):
                raise ValueError(f"source[{idx}] ({title!r}) turn_index {ti} out of range (0..{len(norm_turns) - 1})")
        if s.get("timestamp_seconds") is not None and float(s["timestamp_seconds"]) < 0:
            raise ValueError(f"source[{idx}] ({title!r}) timestamp_seconds must be >= 0")

        for sp_idx, sp in enumerate(s.get("spans") or []):
            if "turn_index" not in sp or "start_offset" not in sp or "end_offset" not in sp or "quote" not in sp:
                raise ValueError(f"source[{idx}] span[{sp_idx}] must include turn_index, start_offset, end_offset, and quote")
            ti = int(sp["turn_index"])
            if not (0 <= ti < len(norm_turns)):
                raise ValueError(f"source[{idx}] span[{sp_idx}] turn_index {ti} out of range (0..{len(norm_turns) - 1})")
            t_text = norm_turns[ti]
            start = int(sp["start_offset"])
            end = int(sp["end_offset"])
            quote = sp["quote"]
            if not isinstance(quote, str) or not quote:
                raise ValueError(f"source[{idx}] span[{sp_idx}] quote must be a non-empty string")
            if not (0 <= start < end <= len(t_text)):
                raise ValueError(
                    f"source[{idx}] span[{sp_idx}] invalid offsets [{start}:{end}] for turn {ti} of length {len(t_text)}"
                )
            actual = t_text[start:end]
            if actual != quote:
                raise ValueError(
                    f"source[{idx}] span[{sp_idx}] quote mismatch at turn {ti}[{start}:{end}]: "
                    f"expected {quote!r}, got {actual!r}"
                )


def _discover_sources_for_episode(repo_root: Path, slug: str, stem: str, md_turn_texts: list[str]) -> list[tuple[str, list[dict], list[str]]]:
    """Find sources.json files for this episode (or any standalone child split in index/splits.json)."""
    found: list[tuple[str, list[dict], list[str]]] = []
    seen_paths: set[Path] = set()
    for candidate_dir in (slug, stem):
        p = (repo_root / "artifacts" / candidate_dir / "sources.json").resolve()
        if p.exists() and p not in seen_paths:
            seen_paths.add(p)
            raw = json.loads(p.read_text(encoding="utf-8"))
            for ep_slug, src_list in normalize_sources_payload(raw, slug):
                found.append((ep_slug, src_list, md_turn_texts))

    splits_path = repo_root / "index" / "splits.json"
    if splits_path.exists():
        try:
            splits_doc = json.loads(splits_path.read_text(encoding="utf-8"))
        except Exception:
            splits_doc = {}
        for sp in splits_doc.get("splits", []):
            if sp.get("parent_slug") == slug or sp.get("parent_stem") == stem:
                sp_slug = sp.get("slug") or ""
                sp_stem = sp.get("stem") or ""
                for candidate_dir in (sp_slug, sp_stem):
                    if not candidate_dir:
                        continue
                    p = (repo_root / "artifacts" / candidate_dir / "sources.json").resolve()
                    if p.exists() and p not in seen_paths:
                        seen_paths.add(p)
                        sp_md_path = repo_root / (sp.get("transcript_path") or f"episodes/{sp_stem}.md")
                        sp_turns = parse_turn_texts(sp_md_path.read_text(encoding="utf-8")) if sp_md_path.exists() else [t.get("text", "") for t in sp.get("turns", [])]
                        raw = json.loads(p.read_text(encoding="utf-8"))
                        for ep_slug, src_list in normalize_sources_payload(raw, sp_slug):
                            found.append((ep_slug, src_list, sp_turns))
    return found


def _emit_sources_sql(out: list[str], eid: str, ep_slug: str, src_list: list[dict], turn_texts: list[str]) -> None:
    validate_sources(src_list, turn_texts)
    if not src_list:
        out.append(f"DELETE FROM sources WHERE episode_id = {eid} AND episode_slug = {q(ep_slug)};")
        return
    urls = [s["url"].strip() for s in src_list]
    out.append(
        f"DELETE FROM sources WHERE episode_id = {eid} AND episode_slug = {q(ep_slug)} "
        f"AND url NOT IN ({', '.join(q(u) for u in urls)});"
    )
    for idx, s in enumerate(src_list, start=1):
        url = s["url"].strip()
        title = s["title"].strip()
        sort_order = int(s.get("sort_order", idx))
        publisher = s.get("publisher")
        domain = s.get("domain") or urllib.parse.urlparse(url).netloc.removeprefix("www.")
        published_date = s.get("published_date")
        description = s.get("description") or ""
        spans = s.get("spans") or []
        quote = s.get("quote") if s.get("quote") is not None else (spans[0]["quote"] if spans else None)
        turn_index = int(s["turn_index"]) if s.get("turn_index") is not None else None
        ts_sec = float(s["timestamp_seconds"]) if s.get("timestamp_seconds") is not None else None
        ts_label = s.get("timestamp_label")
        http_status = int(s["http_status"]) if s.get("http_status") is not None else None
        verified_at = s.get("verified_at")

        out.append(
            "INSERT INTO sources (episode_id, episode_slug, sort_order, title, url, publisher, domain, "
            "published_date, description, quote, turn_index, timestamp_seconds, timestamp_label, http_status, verified_at) "
            f"VALUES ({eid}, {q(ep_slug)}, {q(sort_order)}, {q(title)}, {q(url)}, {q(publisher)}, {q(domain)}, "
            f"{q(published_date)}, {q(description)}, {q(quote)}, {q(turn_index)}, {q(ts_sec)}, {q(ts_label)}, "
            f"{q(http_status)}, {q(verified_at)})\n"
            "  ON CONFLICT(episode_id, url) DO UPDATE SET "
            "episode_slug=excluded.episode_slug, sort_order=excluded.sort_order, title=excluded.title, "
            "publisher=excluded.publisher, domain=excluded.domain, published_date=excluded.published_date, "
            "description=excluded.description, quote=excluded.quote, turn_index=excluded.turn_index, "
            "timestamp_seconds=excluded.timestamp_seconds, timestamp_label=excluded.timestamp_label, "
            "http_status=excluded.http_status, verified_at=excluded.verified_at;"
        )
        sid = f"(SELECT id FROM sources WHERE episode_id = {eid} AND url = {q(url)})"

        out.append(f"DELETE FROM source_people WHERE source_id = {sid};")
        for person in s.get("people") or []:
            if isinstance(person, dict):
                p_name = (person.get("name") or person.get("slug") or "").strip()
                p_slug = (person.get("slug") or slugify(p_name)).strip()
            else:
                p_raw = str(person).strip()
                p_slug = slugify(p_raw)
                p_name = p_raw if p_raw != p_slug else p_raw.replace("-", " ").title()
            if not p_slug:
                continue
            out.append(f"INSERT INTO people (slug, name) VALUES ({q(p_slug)}, {q(p_name)}) ON CONFLICT(slug) DO NOTHING;")
            out.append(
                f"INSERT OR IGNORE INTO source_people (source_id, person_id) "
                f"SELECT {sid}, id FROM people WHERE slug = {q(p_slug)} OR lower(name) = lower({q(p_name)});"
            )

        out.append(f"DELETE FROM source_tags WHERE source_id = {sid};")
        for topic in (s.get("topics") or s.get("tags") or []):
            if isinstance(topic, dict):
                t_raw = (topic.get("value") or topic.get("name") or topic.get("slug") or "").strip()
                t_slug = (topic.get("slug") or slugify(t_raw)).strip()
                t_val = t_raw if t_raw != t_slug else t_raw.replace("-", " ").title()
                t_kind = (topic.get("kind") or "topic").strip()
            else:
                t_raw = str(topic).strip()
                t_slug = slugify(t_raw)
                t_val = t_raw if t_raw != t_slug else t_raw.replace("-", " ").title()
                t_kind = "topic"
            if not t_slug:
                continue
            tag_match = (
                f"kind = {q(t_kind)} AND ("
                f"lower(value) = lower({q(t_raw)}) OR "
                f"lower(value) = lower({q(t_val)}) OR "
                f"lower(value) = lower({q(t_slug)}) OR "
                f"lower(replace(replace(value, ' & ', '-'), ' ', '-')) = {q(t_slug)}"
                ")"
            )
            out.append(
                f"INSERT INTO tags (kind, value) SELECT {q(t_kind)}, {q(t_val)} "
                f"WHERE NOT EXISTS (SELECT 1 FROM tags WHERE {tag_match});"
            )
            out.append(
                f"INSERT OR IGNORE INTO source_tags (source_id, tag_id) "
                f"SELECT {sid}, id FROM tags WHERE {tag_match};"
            )

        out.append(f"DELETE FROM source_spans WHERE source_id = {sid};")
        for sp in spans:
            out.append(
                "INSERT INTO source_spans (source_id, turn_index, start_offset, end_offset, quote) "
                f"VALUES ({sid}, {int(sp['turn_index'])}, {int(sp['start_offset'])}, {int(sp['end_offset'])}, {q(sp['quote'])})\n"
                "  ON CONFLICT(source_id, turn_index, start_offset, end_offset) DO UPDATE SET quote=excluded.quote;"
            )


def build_sql(transcript: Path, repo_root: Path, slug: str, alignment: dict | None = None, revoice: dict | None = None,
              recording: dict | None = None, explicit: bool = True, manifest: dict | None = None,
              episode_number: int | None = None, sources: dict | list | None = None) -> str:
    data = transcript.read_bytes()
    md = data.decode("utf-8")
    fm = front_matter(md)
    turns = parse_transcript(md)
    md_turn_texts = parse_turn_texts(md)
    rel = transcript.resolve().relative_to(repo_root.resolve()).as_posix()
    stem = transcript.stem
    rec = dict(recording or {})
    if alignment is None and revoice is None:
        rec.setdefault("recording_method", "none")
    ep = {
        "slug": slug, "guid": f"ask-eve:{stem}", "date": fm["date"], "title": fm["title"], "explicit": explicit,
        "language": "en", "source_url": fm.get("source"),
        "session_started_at": turns[0].created_at if turns else None,
        "session_ended_at": turns[-1].created_at if turns else None, "turn_count": len(turns),
        "transcript_path": rel, "transcript_sha256": hashlib.sha256(data).hexdigest(), "transcript_bytes": len(data),
    }
    if fm.get("context") or fm.get("summary"):   # episode context note: card blurb + feed description
        ep["summary"] = fm.get("context") or fm["summary"]
    if alignment:
        ep.update({k: alignment.get(k) for k in ("sync_offset_seconds", "alignment_method", "alignment_coverage", "asr_model",
                                                 "recording_started_at", "raw_audio_sha256")})
        ep["alignment_path"] = f"alignment/{stem}.json"
    ep["episode_type"] = episode_type(fm)
    if ep["episode_type"] == "bonus":       # bonus episodes are unnumbered
        ep["episode_number"] = None
    elif episode_number is not None:
        ep["episode_number"] = episode_number
    if revoice:
        ep["tts_voice"] = revoice.get("tts")
        ep["recording_method"] = revoice.get("recording_method", "mic-plus-tts")
        if revoice.get("narrator"):
            ep["narrator_voice"] = revoice["narrator"]
        if revoice.get("ai_disclosure"):
            ep["ai_reconstructed"], ep["ai_disclosure"] = 1, revoice["ai_disclosure"]
        if alignment is None and revoice.get("alignment_method"):
            ep["alignment_method"], ep["alignment_coverage"] = revoice["alignment_method"], revoice["alignment_coverage"]
    if manifest:
        for k in ("audio_key", "audio_url", "audio_bytes", "audio_sha256", "audio_mime", "duration_seconds", "loudness_lufs"):
            if manifest.get(k) is not None:
                ep[k] = manifest[k]
        ep["recording_method"] = manifest.get("recording_method", ep.get("recording_method"))
    if ep.get("recording_method") == "tts-reconstructed":
        ep["ai_reconstructed"] = 1
        nar_v = (ep.get("narrator_voice") or "").lower()
        default_disc = CLONE_AI_DISCLOSURE if ("elevenlabs" in nar_v or "clone" in nar_v) else AI_DISCLOSURE
        ep.setdefault("ai_disclosure", default_disc)
        if alignment is None:      # every turn was placed by the synthesizer: offsets are exact
            ep["alignment_method"], ep["alignment_coverage"] = "tts-exact", 1.0
    for k, v in rec.items():
        ep[k] = v
    if ep.get("audio_key") and not ep.get("audio_url"):
        ep["audio_url"] = AUDIO_BASE + ep["audio_key"]
    cols = list(ep)
    out = ["-- generated by scripts/d1_upsert.py; idempotent (re-running replaces this episode's people + turn offsets)"]
    out.append(f"INSERT INTO episodes ({', '.join(cols)}) VALUES ({', '.join(q(ep[c]) for c in cols)})\n"
               f"  ON CONFLICT(slug) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in cols if c not in ('slug', 'guid'))},"
               " updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now');")
    eid = f"(SELECT id FROM episodes WHERE slug = {q(slug)})"
    for name in fm["people"]:
        out.append(f"INSERT INTO people (slug, name) VALUES ({q(slugify(name))}, {q(name)}) ON CONFLICT(slug) DO NOTHING;")
    out.append(f"DELETE FROM episode_people WHERE episode_id = {eid};")
    for name in fm["people"]:
        out.append(f"INSERT INTO episode_people (episode_id, person_id) SELECT {eid}, id FROM people WHERE slug = {q(slugify(name))};")
    if manifest and manifest.get("chapters"):
        out.append(f"DELETE FROM chapters WHERE episode_id = {eid} AND language = 'en';")
        for c in manifest["chapters"]:
            out.append(f"INSERT INTO chapters (episode_id, language, start_seconds, title) VALUES ({eid}, 'en', {q(float(c['startTime']))}, {q(c['title'])});")
    src = revoice["turns"] if revoice else (alignment["turns"] if alignment else None)
    if src is not None:
        if len(src) != len(turns):
            raise ValueError(f"offsets have {len(src)} turns, transcript has {len(turns)}")
        out.append(f"DELETE FROM turn_offsets WHERE episode_id = {eid};")
        for t, tr in zip(src, turns):
            if t["speaker"] != tr.speaker:
                raise ValueError(f"turn {tr.index}: speaker mismatch {t['speaker']} vs {tr.speaker}")
            method = t.get("edited_method") or t["method"]
            score = t.get("score") if method in ("asr-match", "sync-phrase") else None
            out.append("INSERT INTO turn_offsets (episode_id, turn_index, speaker, created_at, raw_start_seconds, raw_end_seconds,"
                       " edited_start_seconds, edited_end_seconds, method, score) VALUES "
                       f"({eid}, {tr.index}, {q(tr.speaker)}, {q(t.get('created_at') or tr.created_at)}, {q(t.get('raw_start'))},"
                       f" {q(t.get('raw_end'))}, {q(t.get('edited_start'))}, {q(t.get('edited_end'))}, {q(method)}, {q(score)});")

    if sources is not None:
        for ep_slug, src_list in normalize_sources_payload(sources, slug):
            _emit_sources_sql(out, eid, ep_slug, src_list, md_turn_texts)
    else:
        for ep_slug, src_list, target_turns in _discover_sources_for_episode(repo_root, slug, stem, md_turn_texts):
            _emit_sources_sql(out, eid, ep_slug, src_list, target_turns)

    return "\n".join(x for x in out if x) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--transcript", required=True, type=Path)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--alignment", type=Path)
    ap.add_argument("--revoice", type=Path)
    ap.add_argument("--recording", type=Path, help="JSON of extra episode columns (recording_device, raw_audio_key, ...)")
    ap.add_argument("--manifest", type=Path, help="manifest.json from produce_audio.py")
    ap.add_argument("--sources", type=Path, help="sources.json path (auto-discovered under artifacts/<slug>/sources.json if omitted)")
    ap.add_argument("--episode-number", type=int)
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    load = lambda p: json.loads(p.read_text()) if p else None  # noqa: E731
    sql = build_sql(a.transcript, a.repo_root, a.slug, load(a.alignment), load(a.revoice), load(a.recording),
                    manifest=load(a.manifest), episode_number=a.episode_number, sources=load(a.sources))
    if a.out:
        a.out.write_text(sql)
    else:
        sys.stdout.write(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
