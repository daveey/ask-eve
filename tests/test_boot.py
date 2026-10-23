import subprocess, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import boot as B

def ep(d, name, turns, front=True):
    fm = f"---\ndate: {name[:10]}\npeople: [Peter Thiel]\n---\n" if front else ""
    (d / name).write_text(fm + "\n".join(f"[00:00:{i:02d}] **{'David' if i % 2 == 0 else 'Eve'}:** {name} turn {i}"
                                         for i in range(turns)) + "\n")

@pytest.fixture
def eps(tmp_path):
    (tmp_path / "README.md").write_text("# Episodes\nDavid: not a real turn\n")
    return tmp_path

def test_empty_dir(tmp_path):
    assert "no episodes yet" in B.boot(tmp_path)

def test_missing_dir(tmp_path):
    assert "no episodes yet" in B.boot(tmp_path / "nope")

def test_readme_only_is_ignored(eps):
    assert B.latest_episode(eps) is None and "no episodes yet" in B.boot(eps)

def test_single_episode(eps):
    ep(eps, "2026-10-23_first.md", 3)
    out = B.boot(eps).splitlines()
    assert out[0] == "# 2026-10-23_first.md (last 3 turns)"
    assert out[1:] == [f"[00:00:0{i}] {'David' if i % 2 == 0 else 'Eve'}: 2026-10-23_first.md turn {i}" for i in range(3)]

def test_front_matter_excluded(eps):
    ep(eps, "2026-10-23_first.md", 2)
    out = B.boot(eps)
    assert "people:" not in out and "---" not in out and "date:" not in out

def test_latest_by_filename(eps):
    for name in ["2026-10-23_b.md", "2026-11-01_c.md", "2026-09-30_z.md"]:
        ep(eps, name, 2)
    assert B.latest_episode(eps).name == "2026-11-01_c.md"
    out = B.boot(eps)
    assert out.startswith("# 2026-11-01_c.md") and "2026-10-23_b" not in out and "2026-09-30_z" not in out

@pytest.mark.parametrize("n,expected", [(2, 2), (5, 5), (8, 5), (30, 5), (0, 0)])
def test_tail_length(eps, n, expected):
    ep(eps, "2026-10-23_first.md", 5)
    lines = B.boot(eps, n=n).splitlines()
    assert len(lines) == 1 + expected and lines[0].endswith(f"(last {expected} turns)")
    if expected:
        assert lines[-1].endswith("turn 4") and lines[1].endswith(f"turn {5 - expected}")

def test_multiline_turns_and_formats(eps):
    (eps / "2026-10-23_x.md").write_text(
        "---\ntitle: x\n---\nintro line before any turn\n"
        "[00:00:01] **David:** first line\ncontinues here\n\nand after a blank\n"
        "Eve: plain label\n**Eve**: bold name only\n[1:02:03] David: long timestamp\n")
    turns = B.parse_turns((eps / "2026-10-23_x.md").read_text())
    assert turns == [("00:00:01", "David", "first line\ncontinues here\nand after a blank"),
                     (None, "Eve", "plain label"), (None, "Eve", "bold name only"), ("1:02:03", "David", "long timestamp")]
    assert B.boot(eps, n=1).splitlines()[1] == "[1:02:03] David: long timestamp"

def test_non_speaker_lines_not_turns():
    assert B.parse_turns("Davidson: no\nEvelyn: no\n") == []

def run(*args):
    return subprocess.run([sys.executable, str(ROOT / "boot.py"), *args], capture_output=True, text=True, timeout=60)

def test_cli(eps, tmp_path_factory):
    ep(eps, "2026-10-23_first.md", 10)
    out = run("--episodes-dir", str(eps), "-n", "3")
    assert out.returncode == 0 and out.stdout.startswith("# 2026-10-23_first.md (last 3 turns)")
    assert len(out.stdout.strip().splitlines()) == 4
    assert len(run("--episodes-dir", str(eps), "--turns", "4").stdout.strip().splitlines()) == 5
    empty = run("--episodes-dir", str(tmp_path_factory.mktemp("empty")))
    assert empty.returncode == 0 and "no episodes yet" in empty.stdout

def test_cli_default_dir_real_project():  # real episodes/ currently holds only README.md
    out = run()
    assert out.returncode == 0 and out.stdout.strip()
