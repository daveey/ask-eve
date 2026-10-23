"""Metadata-only D1 update (meta.yml): title + transcript sha follow the front matter; guid/audio untouched."""
import hashlib
import sqlite3

from pipeline_helpers import ROOT, EP1
import d1_meta_update as M
import sqlite_apply


def test_meta_update_changes_title_only(tmp_path):
    db = tmp_path / "d.sqlite"
    up = ROOT / "artifacts/2026-10-23_eve-david-podcast-planning/d1_upsert.sql"
    old = up.read_text().replace("'First Encounter'", "'Old title'")
    old_sql = tmp_path / "old.sql"
    old_sql.write_text(old.replace(hashlib.sha256(EP1.read_bytes()).hexdigest(), "0" * 64))
    sqlite_apply.apply(db, ROOT / "migrations", old_sql)
    con = sqlite3.connect(db)
    before = con.execute("SELECT guid, audio_url, audio_bytes, audio_sha256 FROM episodes WHERE slug='eve-david-podcast-planning'").fetchone()
    meta = tmp_path / "meta.sql"
    meta.write_text(M.build_sql(EP1))
    sqlite_apply.apply(db, ROOT / "migrations", meta)
    row = con.execute("SELECT title, transcript_sha256, transcript_bytes, guid, audio_url, audio_bytes, audio_sha256 "
                      "FROM episodes WHERE slug='eve-david-podcast-planning'").fetchone()
    assert row[0] == "First Encounter"
    assert row[1] == hashlib.sha256(EP1.read_bytes()).hexdigest() and row[2] == len(EP1.read_bytes())
    assert row[3:] == before and before[0] == "ask-eve:2026-10-23_eve-david-podcast-planning"


def test_meta_update_sets_summary_from_context():
    sql = M.build_sql(EP1)
    assert "summary='This show wasn''t planned. Late one night David" in sql
    assert "Marc Andreessen and Ben Horowitz''s turn" not in sql      # topics stay in git, not D1


def test_front_matter_block_lists():
    fm = M.front_matter("---\ntitle: T\ncontext: a: b\ntopics:\n  - one: x\n  - two\npeople: [A, B]\n---\nbody")
    assert fm == {"title": "T", "context": "a: b", "topics": ["one: x", "two"], "people": "[A, B]"}
