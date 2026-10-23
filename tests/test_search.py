import os, sqlite3, subprocess, sys
import pytest
from conftest import MEMORY

pytestmark = pytest.mark.needs_model

def by_text(results, needle):
    return next((r for r in results if needle in r["text"]), None)

def counts(db_path):
    db = sqlite3.connect(db_path); db.enable_load_extension(True)
    import sqlite_vec; sqlite_vec.load(db)
    return {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("chunks", "tags", "fts", "vec")}

def tags_for(db_path, needle):
    db = sqlite3.connect(db_path)
    return set(db.execute("SELECT t.kind, t.value FROM tags t JOIN chunks c ON c.id = t.chunk_id "
                          "WHERE c.text LIKE ?", (f"%{needle}%",)).fetchall())

# --- vector layer -----------------------------------------------------------------------------
def test_vector_semantic_match_without_shared_words(search):
    res = search.query("cooking pasta recipe", k=3)  # no word overlaps the spaghetti chunk
    top = res[0]
    assert "spaghetti" in top["text"] and top["layers"] == {"vec": 1}

def test_vector_semantic_finance(search):
    res = search.query("stock market investors profits", k=2)
    assert "earnings" in res[0]["text"] and "vec" in res[0]["layers"]

# --- tag layer ----------------------------------------------------------------------------------
def test_tag_from_front_matter_people(search, corpus):
    assert ("person", "Peter Thiel") in tags_for(os.environ["EVE_DB"], "What drives him")
    hit = by_text(search.query("Thiel", k=10), "What drives him")  # the word 'Thiel' is not in the text
    assert hit and "tag" in hit["layers"] and "fts" not in hit["layers"]

def test_tag_from_front_matter_topics_and_companies(search):
    t = tags_for(os.environ["EVE_DB"], "What drives him")
    assert {("topic", "mimetic desire"), ("company", "Palantir"), ("speaker", "David")} <= t
    hit = by_text(search.query("mimetic", k=10), "What drives him")
    assert hit and "tag" in hit["layers"]

def test_tag_from_people_name_detection(search):
    t = tags_for(os.environ["EVE_DB"], "Pelosi backed")  # misc episode has people: []
    assert {("person", "Nancy Pelosi"), ("person", "Chan"), ("speaker", "Eve")} <= t
    hit = by_text(search.query("Nancy Pelosi", k=10), "Pelosi backed")
    assert hit and "tag" in hit["layers"]

@pytest.mark.xfail(strict=True, reason="known TODO: tag filter is substring match ('Chan' matches 'Chandler')")
def test_tag_filter_is_whole_token(search):
    assert not by_text(search.query("race", k=10, tag="Chan"), "sitcom")  # Chandler Bing chunk leaks in

# --- tag filter ---------------------------------------------------------------------------------
def test_tag_filter_restricts_results(search):
    res = search.query("theory", k=10, tag="Thiel")
    assert res and all("Thiel" in r["text"] or "Girardian" in r["text"] or "drives him" in r["text"] for r in res)
    assert not by_text(res, "spaghetti")

def test_tag_filter_case_insensitive(search):
    assert [r["text"] for r in search.query("theory", tag="thiel")] == [r["text"] for r in search.query("theory", tag="THIEL")]

def test_tag_filter_unknown_tag_returns_empty(search):
    assert search.query("Girardian", tag="Nobody Atall") == []

# --- FTS5 ---------------------------------------------------------------------------------------
def test_fts_rare_proper_noun(search):
    top = search.query("Girardian", k=3)[0]
    assert "Girardian" in top["text"] and top["layers"].get("fts") == 1

def test_fts_exact_phrase(search):
    res = search.query('"Antichrist lectures"', k=5)
    fts_hits = [r for r in res if "fts" in r["layers"]]
    assert len(fts_hits) == 1 and "Antichrist lectures" in fts_hits[0]["text"]

def test_fts_phrase_order_matters(search):  # reversed phrase must not hit via FTS
    assert not any("fts" in r["layers"] for r in search.query('"lectures Antichrist"', k=10))

def test_no_results_layers_for_nonsense(search):
    res = search.query("zzqxvbnm", k=10)  # vec always returns neighbours; fts/tag must not
    assert all(set(r["layers"]) == {"vec"} for r in res)

# --- RRF fusion ---------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["Girardian", "Pelosi succession", "cooking pasta", "Thiel"])
def test_rrf_score_is_sum_of_reciprocal_ranks(search, q):
    res = search.query(q, k=10)
    for r in res:
        assert r["score"] == pytest.approx(sum(1 / (60 + rank) for rank in r["layers"].values()))
        assert all(rank >= 1 for rank in r["layers"].values())
    assert [r["score"] for r in res] == sorted((r["score"] for r in res), reverse=True)

def test_multi_layer_outranks_single_layer(search):
    res = search.query("Pelosi Chan succession", k=10)
    multi = [r for r in res if len(r["layers"]) > 1]
    single = [r for r in res if len(r["layers"]) == 1]
    assert multi and single and res[0] is multi[0] and min(m["score"] for m in multi[:1]) > max(s["score"] for s in single)
    assert "Pelosi backed" in res[0]["text"] and set(res[0]["layers"]) == {"vec", "fts", "tag"}

def test_k_limits_results(search):
    assert len(search.query("Eve", k=2)) == 2

# --- idempotent re-index ------------------------------------------------------------------------
def test_reindex_is_idempotent(search, capsys):
    before, res_before = counts(os.environ["EVE_DB"]), search.query("Girardian theory", k=5)
    search.index(); search.index()
    assert counts(os.environ["EVE_DB"]) == before and before["chunks"] == before["vec"] == before["fts"] > 0
    assert search.query("Girardian theory", k=5) == res_before

# --- input robustness ---------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["", "   ", "\n\t "])
def test_empty_and_whitespace_queries(search, q):
    assert search.query(q) == []

@pytest.mark.parametrize("q", ['"Antichrist', 'Antichrist"', '"a" "b', '"""', '"', '""', '" "'])
def test_unbalanced_quotes_do_not_crash(search, q):
    assert isinstance(search.query(q), list)

@pytest.mark.parametrize("q", ["Giran*", "^Thiel", "people:Thiel", "(Pelosi", "Chan)", "AND", "OR", "NOT",
                               "Thiel AND", "NEAR(a b)", "a + b - c", "{x}", "col:val*", "%_%", "'; DROP TABLE chunks;--",
                               "Pelosi's", "*", "-", "émigré Zürich", "日本語"])
def test_special_characters_do_not_crash(search, q):
    assert isinstance(search.query(q), list)
    assert counts(os.environ["EVE_DB"])["chunks"] > 0

def test_unbalanced_quote_still_finds_term(search):
    assert "fts" in by_text(search.query('"Girardian', k=5), "Girardian")["layers"]

# --- CLI ----------------------------------------------------------------------------------------
def run_cli(corpus, *args):
    return subprocess.run([sys.executable, str(MEMORY / "search.py"), *args], env={**os.environ, **corpus[1]},
                          capture_output=True, text=True, timeout=120)

def test_cli_index_and_query(search, corpus):
    out = run_cli(corpus, "index")
    assert out.returncode == 0 and "indexed" in out.stdout and corpus[1]["EVE_DB"] in out.stdout
    out = run_cli(corpus, "query", "Girardian", "-k", "2")
    assert out.returncode == 0 and "1. [" in out.stdout and "layers=" in out.stdout and "episodes/2099-01-01_thiel.md" in out.stdout

def test_cli_tag_and_bad_input(search, corpus):
    out = run_cli(corpus, "query", "theory", "--tag", "Thiel", "-k", "3")
    assert out.returncode == 0 and "spaghetti" not in out.stdout
    for q in ['"broken', "", "(*^:"]:
        out = run_cli(corpus, "query", q)
        assert out.returncode == 0, out.stderr
    assert "no results" in run_cli(corpus, "query", "   ").stdout
