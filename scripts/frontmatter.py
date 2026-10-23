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
