"""Transcript front-matter parsing shared by the pipeline and the site build (stdlib only)."""
from __future__ import annotations

import re


def parse_front_matter_block(block: str) -> dict:
    """`key: value` lines plus YAML-style block lists (`key:` followed by `  - item` lines, e.g. `topics:`)."""
    fm: dict = {}
    last = None
    for line in block.splitlines():
        item = re.match(r"^\s*-\s+(.*)$", line)
        if item and last is not None and (fm[last] == "" or isinstance(fm[last], list)):
            fm[last] = (fm[last] or []) + [item.group(1).strip()]
            continue
        k, sep, v = line.partition(":")
        if sep and re.fullmatch(r"[a-z_]+", k.strip()):
            last = k.strip()
            fm[last] = v.strip()
    return fm


EPISODE_TYPES = ("full", "bonus")


def episode_type(fm: dict) -> str:
    """`episode_type: full|bonus` (default full). Bonus episodes are never featured and carry no episode number."""
    t = str(fm.get("episode_type") or "full").strip().lower()
    if t not in EPISODE_TYPES:
        raise ValueError(f"episode_type must be one of {EPISODE_TYPES}, not {t!r}")
    return t


def season(fm: dict) -> int:
    """Front-matter `season: N` (positive integer), default 1. Every feed item carries <itunes:season>."""
    v = str(fm.get("season", "") or "").strip()
    if not v:
        return 1
    if not v.isdigit() or int(v) < 1:
        raise ValueError(f"season must be a positive integer, not {v!r}")
    return int(v)


def episode_number(fm: dict) -> int | None:
    """Front-matter `episode: N` for full episodes; bonus episodes have none."""
    if episode_type(fm) == "bonus":
        return None
    v = str(fm.get("episode", "")).strip()
    return int(v) if v.isdigit() else None
