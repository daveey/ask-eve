"""Headless Chromium smoke test for key pages, Guide, Ledger, Sources, and RTL Farsi player layout."""
import functools
import http.server
import socket
import threading

import pytest

from pipeline_helpers import ROOT
import build_site as B
import sqlite_apply


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pw_smoke")
    db = tmp / "d.sqlite"
    for sql in sorted(ROOT.glob("artifacts/*/d1_upsert.sql")):
        sqlite_apply.apply(db, ROOT / "migrations", sql)
    out = tmp / "dist"
    assert B.main(["--db", str(db), "--out", str(out), "--include-drafts"]) == 0

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


def test_playwright_smoke_pages_and_rtl_player(server_url):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = None
        for exe in (None, "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
            try:
                browser = p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
                break
            except Exception:
                continue
        if browser is None:
            pytest.skip("Chromium unavailable")
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # 1. Home page nav includes Guide and Ledger
        page.goto(f"{server_url}/", wait_until="domcontentloaded")
        assert page.locator('nav.site-nav a[href="/guide/"]').count() == 1
        assert page.locator('nav.site-nav a[href="/ledger/"]').count() == 1

        # 2. Guide page renders Origin Story, Cloning Guide, and 83-turn Transcript
        page.goto(f"{server_url}/guide/", wait_until="domcontentloaded")
        assert page.locator("#origin-story").count() == 1
        assert page.locator("#cloning-guide").count() == 1
        assert page.locator("#transcript").count() == 1
        assert page.locator("li.turn").count() == 83

        # 3. Ledger & Monthly report page
        page.goto(f"{server_url}/ledger/", wait_until="domcontentloaded")
        assert page.locator(".ledger-banner").count() == 1
        assert page.locator("#support").count() == 1
        page.goto(f"{server_url}/reports/2026-10/", wait_until="domcontentloaded")
        assert "October 2026" in page.content()

        # 4. Sources & receipts on California High-Speed Rail episode
        page.goto(f"{server_url}/episodes/california-high-speed-rail-and-cap-and-trade/", wait_until="domcontentloaded")
        assert page.locator("#sources .source-card").count() == 6
        assert page.locator("mark.src-span").count() == 7

        # 5. Farsi RTL episode page player scrubber stays LTR and aligned
        page.goto(f"{server_url}/fa/episodes/david-sacks-and-the-anthropic-war/", wait_until="domcontentloaded")
        assert page.locator('html[lang="fa"][dir="rtl"]').count() == 1
        scrubber_dir = page.locator(".player__scrub").evaluate("el => getComputedStyle(el).direction")
        assert scrubber_dir == "ltr"

        browser.close()
