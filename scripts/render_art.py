#!/usr/bin/env python3
"""Render the site's raster art from the same SVG sources build_site.py uses.

Dev-only (needs `pip install playwright && playwright install chromium`); the outputs are committed under
site/static/ so the Cloudflare Pages build stays dependency-free. Re-run after adding an episode to give it its
own social card:

    python scripts/render_art.py --db /path/to/ask-eve.sqlite [--include-drafts]

Writes: favicon.svg, favicon-32.png, apple-touch-icon.png, icon-512.png, og/default.jpg, og/<slug>.jpg,
art/cover-3000.jpg (the 3000x3000 podcast cover; upload it to audio.askevepodcast.com/art/cover-3000.v1.jpg).
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as B  # noqa: E402

STATIC = B.STATIC
FONT = (STATIC / "fonts" / "fraunces-var.woff2").resolve().as_uri()


def doc(body: str, w: int, h: int) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:Fraunces;src:url({FONT}) format("woff2");font-weight:400 800}}
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;background:#0e0c13}}
svg{{display:block}}
</style></head><body>{body}</body></html>"""


def favicon_svg() -> str:
    return B.mark_svg("", "f").replace('<svg class=""', '<svg xmlns="http://www.w3.org/2000/svg"').replace(
        ' aria-hidden="true" focusable="false"', "")


def og_html(title: str, kicker: str, seed: str, label: str) -> str:
    cover = B.cover_svg(seed, label, "c").replace("<svg ", '<svg width="470" height="470" ').replace(">Ask Eve</text>", "></text>")
    mark = B.mark_svg("m", "o").replace("<svg ", '<svg width="64" height="64" ')
    size = 76 if len(title) < 28 else 62 if len(title) < 48 else 52
    return f"""<div style="position:relative;width:1200px;height:630px;background:radial-gradient(900px 600px at 85% 0%,rgba(224,69,155,.35),transparent 60%),radial-gradient(700px 500px at 0% 100%,rgba(255,106,61,.25),transparent 60%),#0e0c13;color:#f3eee8;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif">
<div style="position:absolute;left:72px;top:64px;display:flex;align-items:center;gap:18px">{mark}<span style="font:700 40px/1 Fraunces,serif">Ask Eve</span></div>
<div style="position:absolute;left:72px;top:190px;width:560px">
<div style="font:600 22px/1.3 system-ui,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#ff9466;margin-bottom:22px">{B.e(kicker)}</div>
<div style="font:700 {size}px/1.04 Fraunces,serif;letter-spacing:-.02em">{B.e(title)}</div></div>
<div style="position:absolute;left:72px;bottom:58px;font:500 24px/1.3 system-ui,sans-serif;color:#aaa3b4">A podcast with an AI co-host · askevepodcast.com</div>
<div style="position:absolute;right:72px;top:80px;border-radius:36px;overflow:hidden;box-shadow:0 40px 80px -30px rgba(123,92,255,.8)">{cover}</div>
<div style="position:absolute;left:0;right:0;bottom:0;height:8px;background:linear-gradient(90deg,#ff6a3d,#e0459b 55%,#7b5cff)"></div></div>"""


def show_cover_html(px: int) -> str:
    # The show's square cover: a waveform ring with the E mark in its core and the wordmark below.
    ring_px = int(px * 0.84)
    ring = B.cover_svg("ask-eve-show", "", "c").replace("<svg ", f'<svg width="{ring_px}" height="{ring_px}" ')
    ring = ring.replace('>Ask Eve</text>', '></text>')
    ring = re.sub(r'<rect width="400" height="400"[^>]*/>', '', ring, count=1)
    mark = B.mark_svg("m", "s").replace('fill="url(#sg)"/>', 'fill="none"/>', 1)
    mpx = int(ring_px * 0.25)
    return f"""<div style="position:relative;width:{px}px;height:{px}px;background:radial-gradient(circle at 30% 20%,#2a1d3a,#0c0a12 75%)">
<div style="position:absolute;left:{(px - ring_px) // 2}px;top:{int(px * 0.03)}px">{ring}
<div style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%)">{mark.replace('<svg ', f'<svg width="{mpx}" height="{mpx}" ')}</div></div>
<div style="position:absolute;left:0;right:0;bottom:{int(px * 0.06)}px;text-align:center;font:700 {int(px * 0.115)}px/1 Fraunces,serif;color:#fff;letter-spacing:-.01em">Ask Eve</div></div>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, help="local SQLite pulled from D1 (for per-episode social cards)")
    ap.add_argument("--include-drafts", action="store_true")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    (STATIC / "favicon.svg").write_text(favicon_svg() + "\n")
    (STATIC / "og").mkdir(parents=True, exist_ok=True)
    (STATIC / "art").mkdir(parents=True, exist_ok=True)
    eps = []
    if a.db:
        db = sqlite3.connect(a.db)
        db.row_factory = sqlite3.Row
        where = "" if a.include_drafts else "WHERE status = 'published'"
        eps = [dict(r) for r in db.execute(f"SELECT slug, title, episode_number, episode_type, date FROM episodes {where}")]
    with sync_playwright() as p:
        br = p.chromium.launch()

        def shot(html: str, w: int, h: int, out: Path, jpeg: bool = False) -> None:
            pg = br.new_page(viewport={"width": w, "height": h})
            pg.set_content(doc(html, w, h))
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(150)
            kw = {"type": "jpeg", "quality": 88} if jpeg else {"type": "png"}
            pg.screenshot(path=str(out), **kw)
            pg.close()
            print(out.relative_to(B.ROOT))

        mark = favicon_svg()
        for px, name in ((32, "favicon-32.png"), (180, "apple-touch-icon.png"), (512, "icon-512.png")):
            m = mark.replace("<svg ", f'<svg width="{px}" height="{px}" ').replace('rx="18"', 'rx="0"' if name != "favicon-32.png" else 'rx="18"')
            shot(m, px, px, STATIC / name)
        shot(og_html("Conversations with an AI who argues back", "The podcast", "ask-eve", "EVE"), 1200, 630, STATIC / "og" / "default.jpg", True)
        for ep in eps:
            num = ep["episode_number"]
            if ep["episode_type"] == "bonus":
                kicker, mark = "Bonus", "BONUS"
            else:
                kicker, mark = (f"Episode {num}" if num is not None else "Episode"), f"EP {num if num is not None else '·'}"
            shot(og_html(ep["title"], kicker, ep["slug"], mark),
                 1200, 630, STATIC / "og" / f"{ep['slug']}.jpg", True)
        shot(show_cover_html(3000), 3000, 3000, STATIC / "art" / "cover-3000.jpg", True)
        br.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
