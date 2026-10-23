"""Eve boot context: print the last N speaker turns of the most recent episode transcript.
Usage: python boot.py [-n 30] [--episodes-dir episodes]"""
import argparse, re, sys
from pathlib import Path

TURN = re.compile(r"^\s*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?(?:\*\*)?(David|Eve)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*)$")
DEFAULT_DIR = Path(__file__).resolve().parent / "episodes"

def latest_episode(episodes_dir):
    files = sorted(p for p in Path(episodes_dir).glob("*.md") if p.name.lower() != "readme.md")
    return files[-1] if files else None

def parse_turns(text):
    """Return [(timestamp|None, speaker, text)]; front matter skipped, continuation lines join the previous turn."""
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    turns = []
    for line in text.splitlines():
        if m := TURN.match(line):
            turns.append([m.group(1), m.group(2), m.group(3).strip()])
        elif turns and line.strip():
            turns[-1][2] = (turns[-1][2] + "\n" + line.strip()).strip()
    return [tuple(t) for t in turns]

def boot(episodes_dir=DEFAULT_DIR, n=30):
    ep = latest_episode(episodes_dir)
    if ep is None:
        return f"no episodes yet in {episodes_dir}"
    turns = parse_turns(ep.read_text(errors="ignore"))[-n:] if n > 0 else []
    body = [f"{f'[{ts}] ' if ts else ''}{who}: {said}" for ts, who, said in turns]
    return "\n".join([f"# {ep.name} (last {len(turns)} turns)", *body])

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--turns", type=int, default=30)
    ap.add_argument("--episodes-dir", default=str(DEFAULT_DIR))
    a = ap.parse_args()
    print(boot(a.episodes_dir, a.turns)); sys.exit(0)
