#!/usr/bin/env python3
"""Apply migrations (once) and an upsert SQL file to a LOCAL SQLite file with foreign keys on.
Local stand-in for `wrangler d1 migrations apply` / `wrangler d1 execute` (no network)."""
import argparse
import sqlite3
from pathlib import Path


def apply(db: Path, migrations: Path, sql: Path | None = None) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("CREATE TABLE IF NOT EXISTS d1_migrations (name TEXT PRIMARY KEY)")
    done = {r[0] for r in con.execute("SELECT name FROM d1_migrations")}
    for m in sorted(migrations.glob("*.sql")):
        if m.name not in done:
            con.executescript(m.read_text())
            con.execute("INSERT INTO d1_migrations (name) VALUES (?)", (m.name,))
    if sql:
        con.executescript("BEGIN;\n" + sql.read_text() + "\nCOMMIT;")
    con.commit()
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--migrations", default=Path("migrations"), type=Path)
    ap.add_argument("--sql", type=Path)
    a = ap.parse_args()
    apply(a.db, a.migrations, a.sql)
    print(f"applied to {a.db}")
