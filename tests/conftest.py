"""Fixtures: a throwaway corpus + DB in tmp dirs. The real eve.db and project files are never touched."""
import importlib, os, sys
from pathlib import Path
import pytest

MEMORY = Path(__file__).resolve().parent.parent / "memory"
MODEL = Path(os.environ.get("EVE_MODEL", MEMORY / "models/potion-base-8M"))
sys.path.insert(0, str(MEMORY))

PEOPLE = """# People index
| Person | Eve's stance (one line) | Episodes |
|---|---|---|
| Peter Thiel | Contrarian investor. | (pending) |
| Nancy Pelosi | Succession planner. | (pending) |
| Chan | Preferred successor. | (pending) |
| David Sacks | Scored on three tests. | (pending) |
"""
EPISODES = {
    "2099-01-01_thiel.md": """---
date: 2099-01-01
title: Thiel
people: [Peter Thiel]
topics: [mimetic desire]
companies: [Palantir]
---
[00:00:00] **David:** What drives him, really?

[00:00:05] **Eve:** Girardian scapegoat theory, plus the Antichrist lectures.
""",
    "2099-01-02_misc.md": """---
date: 2099-01-02
title: misc
people: []
---
[00:00:00] **David:** Boiling spaghetti noodles with tomato sauce in the kitchen tonight.

[00:00:09] **Eve:** Pelosi backed Chan in the race, a pure succession move.

[00:00:20] **Eve:** Quarterly earnings guidance disappointed shareholders on the exchange.
""",
    "2099-01-03_friends.md": """---
people: [Chandler Bing]
---
[00:00:00] **Eve:** A sitcom aside about the race for laughs.
""",
}

@pytest.fixture(scope="session")
def model_dir():
    if not (MODEL / "config.json").exists():
        pytest.skip(f"local model missing at {MODEL}; run the one-time download in README.md")
    return MODEL

@pytest.fixture(scope="session")
def corpus(tmp_path_factory, model_dir):
    root = tmp_path_factory.mktemp("corpus")
    (root / "index").mkdir(); (root / "episodes").mkdir(); (root / "memory").mkdir()
    (root / "index/people.md").write_text(PEOPLE)
    for name, body in EPISODES.items():
        (root / "episodes" / name).write_text(body)
    env = {"EVE_ROOT": str(root), "EVE_DB": str(root / "memory/test.db"), "EVE_MODEL": str(model_dir)}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    yield root, env
    for k, v in old.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

@pytest.fixture(scope="session")
def search(corpus):
    mod = importlib.import_module("search")
    mod.index()
    return mod
