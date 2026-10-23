"""Ask Eve hybrid retrieval: sqlite-vec vectors + tags table + FTS5 in one SQLite file.
Fusion: Reciprocal Rank Fusion (RRF, k=60) -- each layer ranks independently, a chunk scores
sum(1/(60+rank)) over layers it appears in, so raw scores never need cross-layer normalization.
Paths: EVE_ROOT (corpus root), EVE_DB (database), EVE_MODEL (local model dir) env vars override defaults."""
import argparse, os, re, sqlite3, struct, sys
from pathlib import Path
import sqlite_vec, yaml
from model2vec import StaticModel

HERE = Path(__file__).resolve().parent
ROOT = lambda: Path(os.environ.get("EVE_ROOT", HERE.parent))
DB = lambda: Path(os.environ.get("EVE_DB", ROOT() / "memory/eve.db"))
MODEL = lambda: Path(os.environ.get("EVE_MODEL", HERE / "models/potion-base-8M"))
SKIP = (".", "memory/models", "memory/README.md", "memory/persistent-facts.example.md", "tests")
RRF_K, POOL, _model = 60, 200, {}
KINDS = {"people": "person", "companies": "company", "topics": "topic", "stances": "stance"}  # front-matter keys

def embed(texts):  # local model dir only; refuse a missing dir rather than fall back to the HF hub
    if (key := str(MODEL())) not in _model:
        if not Path(key, "config.json").exists(): raise FileNotFoundError(f"model not found: {key}")
        _model[key] = StaticModel.from_pretrained(key)
    return [struct.pack(f"{v.size}f", *v.tolist()) for v in _model[key].encode(texts)]

def connect():
    db = sqlite3.connect(DB())
    db.enable_load_extension(True); sqlite_vec.load(db); db.enable_load_extension(False)
    return db

def fts_query(text):  # balanced "phrases" kept; stray quotes -> space; every bare token quoted literally
    phrases = [" ".join(re.findall(r"\w+", p)) for p in re.findall(r'"([^"]*)"', text)]
    bare = re.findall(r"\w+", re.sub(r'"[^"]*"', " ", text).replace('"', " "))
    return " OR ".join(f'"{t}"' for t in [p for p in phrases if p] + bare)

def known_people():  # (canonical name, [aliases]) from the index/people.md table
    f = ROOT() / "index/people.md"
    rows = re.findall(r"^\|\s*([^|]+?)\s*\|", f.read_text(), re.M) if f.exists() else []
    return [(n, {n, n.split()[-1]}) for n in rows if n != "Person" and not set(n) <= set("-: ")]

def chunks(text):  # speaker turns / table rows become one chunk each, else paragraphs
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if lines and all(re.match(r"\s*(\||\[\d|\*\*\w+:\*\*)", l) for l in lines):
            yield from (l.strip() for l in lines if not re.match(r"\|[-:| ]+\|$", l.strip()))
        elif lines:
            yield "\n".join(lines)

def index():
    db, people, root = connect(), known_people(), ROOT()
    db.executescript("""DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS tags;
      DROP TABLE IF EXISTS fts; DROP TABLE IF EXISTS vec;
      CREATE TABLE chunks(id INTEGER PRIMARY KEY, path TEXT, text TEXT);
      CREATE TABLE tags(chunk_id INT, kind TEXT, value TEXT COLLATE NOCASE);
      CREATE INDEX tags_v ON tags(value);
      CREATE VIRTUAL TABLE fts USING fts5(text, content='chunks', content_rowid='id');
      CREATE VIRTUAL TABLE vec USING vec0(embedding float[256] distance_metric=cosine);""")
    rows = []
    for f in sorted(root.rglob("*.md")):
        rel = str(f.relative_to(root))
        if rel.startswith(SKIP): continue
        text, meta = f.read_text(errors="ignore"), {}
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        if m: meta, text = yaml.safe_load(m.group(1)) or {}, text[m.end():]
        for c in chunks(text):
            t = {(k, v) for f, k in KINDS.items() for v in meta.get(f) or []}
            # TODO(tags): surname aliasing tags any lone token (e.g. "Chan") as that person; needs
            # whole-token / canonical-ID matching. Tune once real transcripts exist.
            t |= {("person", n) for n, al in people if any(re.search(rf"\b{re.escape(a)}\b", c) for a in al)}
            if s := re.match(r"(?:\[[\d:]+\]\s*)?\*\*(\w+):\*\*", c): t.add(("speaker", s.group(1)))
            rows.append((rel, c, t))
    for (rel, c, t), v in zip(rows, embed([r[1] for r in rows]) if rows else []):
        cid = db.execute("INSERT INTO chunks(path, text) VALUES (?,?)", (rel, c)).lastrowid
        db.executemany("INSERT INTO tags VALUES (?,?,?)", [(cid, k, str(x)) for k, x in t])
        db.execute("INSERT INTO vec(rowid, embedding) VALUES (?,?)", (cid, v))
    db.execute("INSERT INTO fts(fts) VALUES ('rebuild')"); db.commit()
    print(f"indexed {len(rows)} chunks from {len({r[0] for r in rows})} files -> {DB()}")

def query(text, k=5, tag=None):
    """Return [{path, text, score, layers: {layer: 1-based rank}}] fused by RRF over vector, tag and FTS5 layers."""
    if not text or not text.strip(): return []
    db, words, fq = connect(), re.findall(r"\w+", text), fts_query(text)
    # TODO(vec cutoff): vec0 KNN always returns the nearest neighbours, so a low-relevance tail
    # pollutes fused results; add a max cosine distance, tuned against real transcripts.
    ranked = {"vec": [r[0] for r in db.execute(
        "SELECT rowid FROM vec WHERE embedding MATCH ? AND k = ? ORDER BY distance", (embed([text])[0], POOL))]}
    ranked["fts"] = [r[0] for r in db.execute(
        "SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?", (fq, POOL))] if fq else []
    # TODO(tag match): substring LIKE means 'Chan' also matches 'Chandler'; switch to whole-token /
    # canonical-ID matching once real transcripts exist.
    terms = [w for w in words if len(w) > 3] + [text.strip()]  # a tag hits if any query word is in it
    ranked["tag"] = [r[0] for r in db.execute(
        f"SELECT chunk_id FROM tags WHERE {' OR '.join(['value LIKE ?'] * len(terms))} GROUP BY chunk_id "
        "ORDER BY count(*) DESC LIMIT ?", [f"%{w}%" for w in terms] + [POOL])]
    allowed = tag and {r[0] for r in db.execute("SELECT chunk_id FROM tags WHERE value LIKE ?", (f"%{tag}%",))}
    fused = {}
    for layer, ids in ranked.items():
        for rank, cid in enumerate(i for i in ids if not tag or i in allowed):
            fused.setdefault(cid, {})[layer] = rank + 1
    top = sorted(((sum(1 / (RRF_K + r) for r in ls.values()), cid, ls) for cid, ls in fused.items()), reverse=True)
    return [dict(zip(("path", "text"), db.execute("SELECT path, text FROM chunks WHERE id=?", (cid,)).fetchone()),
                 score=s, layers=ls) for s, cid, ls in top[:k]]

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True); sub.add_parser("index")
    q = sub.add_parser("query"); q.add_argument("text"); q.add_argument("--tag"); q.add_argument("-k", type=int, default=5)
    a = ap.parse_args()
    if a.cmd == "index": sys.exit(index())
    for i, r in enumerate(res := query(a.text, a.k, a.tag), 1):
        snip = " ".join(r["text"].split())[:160]
        print(f"{i}. [{r['score']:.4f}] {r['path']}  layers={'+'.join(r['layers'])}\n   {snip}")
    if not res: print("no results")
