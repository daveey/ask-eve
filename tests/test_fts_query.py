"""Pure sanitizer tests: no model needed. Every output must be valid FTS5 syntax."""
import sqlite3
import pytest
search = pytest.importorskip("search")

@pytest.fixture(scope="module")
def fts():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE f USING fts5(text)")
    db.execute("INSERT INTO f VALUES ('the Girardian theory of mimetic desire')")
    return db

@pytest.mark.parametrize("q,expected", [
    ("Girardian", '"Girardian"'),
    ('x "foo bar" y', '"foo bar" OR "x" OR "y"'),
    ('"unbalanced phrase', '"unbalanced" OR "phrase"'),
    ('"a" "b', '"a" OR "b"'),
    ("AND OR NOT", '"AND" OR "OR" OR "NOT"'),
    ("a* ^b :c (d)", '"a" OR "b" OR "c" OR "d"'),
    ('"(Thiel*)"', '"Thiel"'),
    ("", ""), ("   ", ""), ('""', ""), ("*^():", ""),
])
def test_fts_query_sanitizes(q, expected):
    assert search.fts_query(q) == expected

@pytest.mark.parametrize("q", ['"', '"""', 'a"b"c"', "NEAR(a b)", "x:y", "-z", "+", "{a b}", "a AND", "Pelosi's",
                               "'; DROP TABLE f;--", "日本語 émigré"])
def test_fts_query_always_valid_fts5(fts, q):
    s = search.fts_query(q)
    if s: fts.execute("SELECT rowid FROM f WHERE f MATCH ?", (s,)).fetchall()

def test_phrase_query_matches(fts):
    assert fts.execute("SELECT count(*) FROM f WHERE f MATCH ?", (search.fts_query('"mimetic desire"'),)).fetchone()[0] == 1
    assert fts.execute("SELECT count(*) FROM f WHERE f MATCH ?", (search.fts_query('"desire mimetic"'),)).fetchone()[0] == 0
