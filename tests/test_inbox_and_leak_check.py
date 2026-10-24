"""Tests for Eve's inbox, reading list, leak checker (Python + Worker JS), D1 schema, and admin console gate."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_site as B  # noqa: E402
import leak_check as LC  # noqa: E402
import sqlite_apply  # noqa: E402


def test_clean_draft_passes_and_computes_deterministic_sha256():
    res = LC.leak_check(
        to_addr="listener@example.org",
        subject="Re: René Girard on mimetic rivalry",
        body_text=(
            "Hi Alex,\n\n"
            "Thank you for sending this paper to eve@askevepodcast.com! "
            "I've added it to our private reading queue so David and I can review it before the next episode.\n\n"
            "— Eve\nhttps://askevepodcast.com/eve/#reading"
        ),
        private_terms=["SecretStreetName", "private-handle-99"],
    )
    assert res["passed"] is True
    assert res["findings"] == []
    assert len(res["leak_check_sha256"]) == 64


@pytest.mark.parametrize(
    "bad_snippet,expected_rule",
    [
        # Personal / third-party email addresses
        ("Forwarding to person_test@gmail.com for review", "unauthorized_email"),
        ("Reach me at someone@dbloom.in anytime", "unauthorized_email"),
        ("CCing colleague@othercompany.io on this thread", "unauthorized_email"),
        # API keys and tokens
        ("My key is xai-" + "A" * 24, "api_key_or_token"),
        ("Token sk-proj-" + "B" * 24, "api_key_or_token"),
        ("GitHub ghp_" + "c" * 30, "api_key_or_token"),
        ("PAT github_pat_" + "d" * 28, "api_key_or_token"),
        ("CF token cfut_" + "e" * 28, "api_key_or_token"),
        ("AWS AKIA" + "F" * 16, "api_key_or_token"),
        ("Zone token zone_fm_" + "1" * 24, "api_key_or_token"),
        ("Hex secret a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4", "api_key_or_token"),
        # Internal UUIDs, agent IDs, canvas & share links
        ("Agent sand-subagent-0197f3a2b4c5 started", "internal_id_or_private_url"),
        ("Store a5b26010-7059-4a7e-a876-adbefdaad242 mounted", "internal_id_or_private_url"),
        ("Path store-a5b26010-7059-4a7e", "internal_id_or_private_url"),
        ("See https://cursor.com/canvas/abc123xyz", "internal_id_or_private_url"),
        ("From https://grok.com/share/c2hhcmQtMg_12345", "internal_id_or_private_url"),
        # Local filesystem paths
        ("Saved under /home/box/agent-data/notes.md", "local_filesystem_path"),
        ("Built in /workspace/ask-eve-cleanup/site", "local_filesystem_path"),
        ("Mounted at /cursor/stores/my-store", "local_filesystem_path"),
        # Private tailnet & cloud bucket / endpoint names
        ("Host box.tail1234.ts.net is online", "private_hostname_or_infra"),
        ("Stored in R2 bucket ask-eve-masters/inbox/123", "private_hostname_or_infra"),
        ("Endpoint https://upload.askevepodcast.com/ingest", "private_hostname_or_infra"),
    ],
)
def test_leak_check_blocks_every_sensitive_category(bad_snippet: str, expected_rule: str):
    res = LC.leak_check(
        to_addr="listener@example.org",
        subject="Re: Your reading list suggestion",
        body_text=f"Hi there,\n\n{bad_snippet}\n\n— Eve",
    )
    assert res["passed"] is False, f"Expected block for: {bad_snippet}"
    rules = {f["rule"] for f in res["findings"]}
    assert expected_rule in rules, (bad_snippet, res["findings"])


def test_leak_check_blocks_custom_private_terms_from_env():
    res = LC.leak_check(
        to_addr="listener@example.org",
        subject="Re: Question",
        body_text="We recorded this near ChestnutHillPrivateRoad last week.",
        private_terms="ChestnutHillPrivateRoad, AnotherPrivateTerm",
    )
    assert res["passed"] is False
    assert any(f["rule"] == "private_leak_term" for f in res["findings"])


def test_python_and_js_leak_check_sha256_and_verdict_parity():
    to_addr = "reader@example.org"
    subject = "Re: Article submission"
    body_text = "Thanks for sending this to eve@askevepodcast.com! Added to our queue."
    py_res = LC.leak_check(to_addr, subject, body_text, private_terms="SecretTermAlpha")
    js_code = f"""
    import {{ leakCheck }} from './workers/eve-inbox/leak_check.js';
    const r = await leakCheck(
      {{ to_addr: {json.dumps(to_addr)}, subject: {json.dumps(subject)}, body_text: {json.dumps(body_text)} }},
      {{ PRIVATE_LEAK_TERMS: 'SecretTermAlpha' }}
    );
    console.log(JSON.stringify(r));
    """
    out = subprocess.check_output(["node", "--input-type=module", "-e", js_code], cwd=ROOT, text=True)
    js_res = json.loads(out)
    assert js_res["passed"] == py_res["passed"] == True
    assert js_res["leak_check_sha256"] == py_res["leak_check_sha256"]


def test_d1_migration_0002_enforces_send_gate_constraints(tmp_path: Path):
    db = tmp_path / "d1.sqlite"
    sqlite_apply.apply(db, ROOT / "migrations")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        """INSERT INTO inbox_items (id, received_at, from_addr, to_addr, subject, status)
           VALUES ('inb_1', '2026-10-24T01:00:00Z', 'reader@example.org', 'eve@askevepodcast.com', 'Subj', 'ready')"""
    )
    # Inserting a draft marked 'sent' with leak_check_passed=0 must fail the SQLite CHECK constraint
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            """INSERT INTO draft_replies (id, inbox_item_id, to_addr, subject, body_text, created_at, updated_at,
               leak_check_passed, leak_check_findings, leak_check_sha256, status)
               VALUES ('drf_bad', 'inb_1', 'reader@example.org', 'Re: Subj', 'Body', '2026-10-24T01:00:00Z',
               '2026-10-24T01:00:00Z', 0, '[]', '', 'sent')"""
        )
    # Inserting a valid leak-checked draft succeeds
    sha = LC.compute_draft_sha256("reader@example.org", "Re: Subj", "Clean body")
    con.execute(
        """INSERT INTO draft_replies (id, inbox_item_id, to_addr, subject, body_text, created_at, updated_at,
           leak_check_passed, leak_check_findings, leak_check_sha256, status)
           VALUES ('drf_ok', 'inb_1', 'reader@example.org', 'Re: Subj', 'Clean body', '2026-10-24T01:00:00Z',
           '2026-10-24T01:00:00Z', 1, '[]', ?, 'draft')""",
        (sha,),
    )
    # Verify the 2 seeded @askevepodcast launch posts in x_drafts
    seeded = con.execute("SELECT id, body, leak_check_passed, status FROM x_drafts ORDER BY id").fetchall()
    assert len(seeded) >= 2
    for xid, body, passed, status in seeded:
        assert len(body) <= 280, xid
        assert passed == 1, xid
        assert status == "draft", xid
        assert LC.leak_check("@askevepodcast", "X post from @askevepodcast", body)["passed"] is True

    # Inserting an x_drafts row with status='approved_sent' and leak_check_passed=0 or >280 chars must fail CHECK
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            """INSERT INTO x_drafts (id, body, leak_check_passed, leak_check_findings_json, leak_check_sha256, status, created_at)
               VALUES ('xd_bad', 'Unchecked body', 0, '[]', '', 'approved_sent', '2026-10-24T01:00:00Z')"""
        )
    con.commit()


def test_worker_node_test_suite_passes():
    res = subprocess.run(
        ["node", "--test", "test/index.test.mjs"],
        cwd=ROOT / "workers/eve-inbox",
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + "\n" + res.stderr


def test_public_reading_list_renders_and_private_queue_never_exports(tmp_path: Path):
    db = tmp_path / "site.sqlite"
    for sql in sorted(ROOT.glob("artifacts/*/d1_upsert.sql")):
        sqlite_apply.apply(db, ROOT / "migrations", sql)
    con = sqlite3.connect(db)
    # Insert a private inbox item & draft reply containing sensitive sentinel text
    con.execute(
        """INSERT INTO inbox_items (id, received_at, from_addr, from_name, to_addr, subject, text_body, status)
           VALUES ('inb_priv', '2026-10-24T02:00:00Z', 'secret_sender@example.org', 'Secret Sender',
                   'eve@askevepodcast.com', 'TOP_SECRET_UNPUBLISHED_QUEUE_ITEM', 'Private body 999111', 'ready')"""
    )
    # Also insert a promoted public reading_list item in D1
    con.execute(
        """INSERT INTO reading_list (id, slug, title, url, author_or_source, submitted_via, added_at, status, eve_notes, episode_slug)
           VALUES ('rd_promoted', 'compute-governance-paper', 'Compute Governance and Frontier Models',
                   'https://arxiv.org/abs/2402.08797', 'Lennart Heim et al.', 'listener email',
                   '2026-10-24T02:10:00Z', 'reading',
                   'A rigorous breakdown of hardware-enabled governance and why compute thresholds are both measurable and gameable.',
                   'first-encounter')"""
    )
    con.commit()

    out = tmp_path / "dist"
    # Note: we also add the promoted item's strings to ru runtime or test English + check private item absence
    items = B.load_reading_list(db)
    slugs = {it["slug"] for it in items}
    assert "the-techno-optimist-manifesto" in slugs
    assert "things-hidden-since-the-foundation-of-the-world" in slugs
    assert "compute-governance-paper" in slugs

    # Remove the extra D1 row before full build so i18n strict check on untranslated D1 row doesn't complain,
    # while keeping the private inbox_items row in DB to prove inbox_items is NEVER exported to site/dist!
    con.execute("DELETE FROM reading_list WHERE id = 'rd_promoted'")
    con.commit()

    assert B.main(["--db", str(db), "--out", str(out), "--include-drafts"]) == 0
    eve_html = (out / "eve/reading/index.html").read_text()
    ru_eve_html = (out / "ru/eve/reading/index.html").read_text()
    assert 'id="reading"' in eve_html
    assert 'id="reading-list"' in eve_html
    assert "Eve&#x27;s Reading List" in eve_html or "Eve's Reading List" in eve_html
    assert "The Techno-Optimist Manifesto" in eve_html
    assert "https://x.com/intent/tweet?text=%40askevepodcast%20" in eve_html
    assert "@askevepodcast" in eve_html
    assert "mailto:eve@askevepodcast.com" in eve_html
    assert "mailto:reading@askevepodcast.com" in eve_html
    assert "Список чтения Евы" in ru_eve_html
    assert (out / "reading/index.html").is_file()
    assert (out / "reading-list/index.html").is_file()

    # Verify private inbox item never appears anywhere in the built site
    for f in out.rglob("*"):
        if f.is_file() and f.suffix in {".html", ".xml", ".txt", ".json"}:
            txt = f.read_text(errors="ignore")
            assert "TOP_SECRET_UNPUBLISHED_QUEUE_ITEM" not in txt
            assert "Private body 999111" not in txt
