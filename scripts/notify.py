#!/usr/bin/env python3
"""Tell David an ingest finished (or failed): one email via the upload Worker's POST /notify.

Run by ingest.yml as its last step (if: always()). Reads whatever the run produced, so it works after a
failure at any step: artifacts/<stem>/manifest.json + alignment.json, the Drive results written by
scripts/gdrive.py, the PR URL and the run URL. Env:
  NOTIFY_TOKEN   bearer for /notify (GitHub secret; also a Worker secret). Missing -> prints and exits 0.
  NOTIFY_URL     default https://upload.askevepodcast.com/notify
  JOB_STATUS     success | failure | cancelled (GitHub's job.status)
  STEM, PR_URL, RUN_URL, DRIVE_JSON (comma-separated result files from gdrive.py)

  python scripts/notify.py [--dry-run]     # --dry-run prints the message instead of sending it
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _load(p: str | Path) -> dict:
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}


def build(env: dict, root: Path = Path(".")) -> dict:
    stem = env.get("STEM", "?")
    status = env.get("JOB_STATUS", "unknown")
    art = root / "artifacts" / stem
    if not art.is_dir():
        art = root / "out" / stem / "artifacts"
    m, al = _load(art / "manifest.json"), _load(art / "alignment.json")
    drive_files, folder = [], None
    for p in filter(None, (env.get("DRIVE_JSON") or "").split(",")):
        d = _load(root / p)
        folder = folder or (d.get("folder") or {}).get("url")
        drive_files += d.get("files", [])
    mp3 = next((f for f in drive_files if f["name"].endswith(".mp3")), None)

    lines = []
    if status == "success":
        subject = f"Ask Eve: {stem} is ready"
        lines.append(f"Episode {stem} is processed and waiting for your review.")
    else:
        subject = f"Ask Eve: ingest {status} for {stem}"
        lines.append(f"The ingest of {stem} did not finish ({status}). Your recording is safe in the private bucket"
                     + (" and in Google Drive." if drive_files else "."))
    lines.append("")
    if mp3:
        lines.append(f"Listen (Google Drive): {mp3['url']}")
    if folder:
        lines.append(f"All files (Google Drive): {folder}")
    if env.get("PR_URL"):
        lines.append(f"Review + merge: {env['PR_URL']}")
    if env.get("RUN_URL"):
        lines.append(f"Run log: {env['RUN_URL']}")
    if m:
        mins = round(float(m.get("duration_seconds", 0)) / 60, 1)
        lines += ["", f"Length: {mins} min, {m.get('loudness_lufs', '?')} LUFS, recording: {m.get('recording_method', '?')}",
                  "Eve's voice: " + ("PLACEHOLDER (no TTS key) - publish will refuse it" if m.get("placeholder_audio") else "real xAI TTS")]
    if al:
        lines.append(f"Alignment: {al.get('david_matched', '?')}/{al.get('david_turns', '?')} of your turns matched"
                     f" (coverage {al.get('alignment_coverage', '?')})")
    if status == "success":
        lines += ["", "Merging the PR does not publish; run publish.yml after you've listened."]
    return {"subject": subject, "text": "\n".join(lines) + "\n"}


def send(msg: dict, url: str, token: str) -> int:
    req = urllib.request.Request(url, data=json.dumps(msg).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "User-Agent": "ask-eve-ingest"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https Worker URL or test stub)
            print(f"notify: HTTP {r.status} {r.read()[:200].decode(errors='replace')}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"notify: HTTP {e.code} {e.read()[:200].decode(errors='replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"notify: {e.reason}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    msg = build(dict(os.environ))
    if a.dry_run:
        print(json.dumps(msg, indent=2))
        return 0
    token = os.environ.get("NOTIFY_TOKEN", "")
    if not token:
        print("notify: NOTIFY_TOKEN not set; skipping (message below)")
        print(msg["subject"])
        return 0
    return send(msg, os.environ.get("NOTIFY_URL") or "https://upload.askevepodcast.com/notify", token)


if __name__ == "__main__":
    raise SystemExit(main())
