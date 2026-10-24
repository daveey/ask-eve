#!/usr/bin/env python3
"""Verify that every primary audio track, RSS enclosure, and clip asset referenced by the built site
is reachable with HTTP 200 and a non-trivial Content-Length on live production."""
import argparse
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIST = ROOT / "site" / "dist"
DEFAULT_SITE_URL = "https://askevepodcast.com"
LANGS = ("en", "zh", "ru", "es", "de", "pt", "fa", "fr")
MIN_AUDIO_BYTES = 10_000
MIN_CLIP_BYTES = 1_000


def collect_urls_from_dist(dist: Path, site_url: str) -> tuple[set[str], set[str], list[str]]:
    """Return (audio_urls, clip_urls, structural_errors) parsed from dist/."""
    audio_urls: set[str] = set()
    clip_urls: set[str] = set()
    errors: list[str] = []

    en_eps_dir = dist / "episodes"
    if not en_eps_dir.is_dir():
        errors.append(f"Missing {en_eps_dir}")
        return audio_urls, clip_urls, errors

    slugs = sorted(
        p.name
        for p in en_eps_dir.iterdir()
        if p.is_dir() and (p / "index.html").is_file() and 'http-equiv="refresh"' not in (p / "index.html").read_text()
    )
    if not slugs:
        errors.append("No episode pages found in dist/episodes/")

    # 1. Episode pages across all 8 languages
    for lang in LANGS:
        prefix = dist if lang == "en" else (dist / lang)
        for slug in slugs:
            p = prefix / "episodes" / slug / "index.html"
            if not p.is_file():
                errors.append(f"Missing episode page: {p.relative_to(dist)}")
                continue
            html = p.read_text()
            m_src = re.search(r'<audio[^>]*>\s*<source src="([^"]+)"', html)
            if not m_src:
                errors.append(f"Missing primary <source src=...> in {p.relative_to(dist)}")
                continue
            url = m_src.group(1)
            if lang != "en" and f"/audio/{lang}/" not in url:
                errors.append(f"{lang} episode page {p.relative_to(dist)} does not use /audio/{lang}/ primary track: {url}")
            audio_urls.add(url)

    # 2. RSS feeds (/feed.xml and /<lang>/feed.xml)
    for lang in LANGS:
        feed_p = (dist / "feed.xml") if lang == "en" else (dist / lang / "feed.xml")
        if not feed_p.is_file():
            errors.append(f"Missing RSS feed: {feed_p.relative_to(dist)}")
            continue
        feed_xml = feed_p.read_text()
        enclosures = re.findall(r'<enclosure\s+url="([^"]+)"', feed_xml)
        if not enclosures:
            errors.append(f"No <enclosure> tags found in {feed_p.relative_to(dist)}")
        for enc_url in enclosures:
            if lang != "en" and f"/audio/{lang}/" not in enc_url:
                errors.append(f"{lang} feed {feed_p.relative_to(dist)} has non-{lang} enclosure: {enc_url}")
            audio_urls.add(enc_url)

    # 3. Clip video and poster URLs on /index.html and /clips/index.html
    for rel in ("index.html", "clips/index.html"):
        p = dist / rel
        if not p.is_file():
            errors.append(f"Missing {rel}")
            continue
        html = p.read_text()
        for v in re.findall(r'<video[^>]+>', html):
            m_v = re.search(r'src="([^"]+)"', v)
            m_p = re.search(r'poster="([^"]+)"', v)
            if m_v:
                clip_urls.add(urljoin(site_url.rstrip("/") + "/", m_v.group(1).lstrip("/")))
            if m_p:
                clip_urls.add(urljoin(site_url.rstrip("/") + "/", m_p.group(1).lstrip("/")))

    return audio_urls, clip_urls, errors


def head_check(url: str) -> tuple[str, int, int, str]:
    """Perform an HTTP HEAD request and return (url, status_code, content_length, error_msg)."""
    try:
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": "AskEve-LiveMediaVerifier/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            cl = int(resp.headers.get("Content-Length", "0") or "0")
            return (url, resp.status, cl, "")
    except Exception as exc:
        code = getattr(exc, "code", 0) or 0
        return (url, code, 0, str(exc))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dist", type=Path, default=DEFAULT_DIST, help="Path to built site/dist directory")
    ap.add_argument("--site-url", default=DEFAULT_SITE_URL, help="Base live site URL for relative clip paths")
    args = ap.parse_args(argv)

    audio_urls, clip_urls, errors = collect_urls_from_dist(args.dist, args.site_url)
    if errors:
        for err in errors:
            print(f"STRUCTURAL ERROR: {err}", file=sys.stderr)
        return 1

    print(f"Checking {len(audio_urls)} unique primary audio URLs and {len(clip_urls)} clip asset URLs...")

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for url, status, cl, err in pool.map(head_check, sorted(audio_urls)):
            if status != 200 or cl < MIN_AUDIO_BYTES:
                failures.append(f"AUDIO FAIL [{status}, {cl} bytes]: {url} {err}".strip())
            else:
                print(f"  OK AUDIO [{status}, {cl:,} bytes]: {url}")

        for url, status, cl, err in pool.map(head_check, sorted(clip_urls)):
            if status != 200 or cl < MIN_CLIP_BYTES:
                failures.append(f"CLIP FAIL [{status}, {cl} bytes]: {url} {err}".strip())
            else:
                print(f"  OK CLIP  [{status}, {cl:,} bytes]: {url}")

    if failures:
        print(f"\nFAILED: {len(failures)} live media check(s) failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"\nSUCCESS: All {len(audio_urls)} audio tracks and {len(clip_urls)} clip assets returned HTTP 200 with valid Content-Length!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
