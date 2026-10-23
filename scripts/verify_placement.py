#!/usr/bin/env python3
"""Compare where turns were PLACED in a generated mix (timeline) with where ASR FOUND them
(align_audio.py --match-speakers David,Eve over the same mix). Reports coverage and timing error."""
import argparse
import json
import statistics
from pathlib import Path


def check(timeline: dict, alignment: dict, tol: float = 2.0) -> dict:
    placed = {t["i"]: t for t in timeline["turns"]}
    errs, bad = [], []
    for t in alignment["turns"]:
        if t["method"] not in ("asr-match", "sync-phrase"):
            continue
        p = placed.get(t["i"])
        if not p or p.get("edited_start") is None:
            continue
        e = abs(t["raw_start"] - p["edited_start"])
        errs.append(e)
        if e > tol:
            bad.append({"i": t["i"], "speaker": t["speaker"], "placed": p["edited_start"], "found": t["raw_start"]})
    errs.sort()
    return {
        "alignment_coverage": alignment["alignment_coverage"], "matchable": alignment["david_matchable"],
        "matched": alignment["david_matched"], "asr_model": alignment.get("asr_model"),
        "median_abs_error_s": round(statistics.median(errs), 3) if errs else None,
        "p95_abs_error_s": round(errs[int(0.95 * (len(errs) - 1))], 3) if errs else None,
        "max_abs_error_s": round(errs[-1], 3) if errs else None,
        "tolerance_s": tol, "outside_tolerance": bad,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeline", required=True, type=Path)
    ap.add_argument("--alignment", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--tolerance", type=float, default=2.0)
    a = ap.parse_args()
    r = check(json.loads(a.timeline.read_text()), json.loads(a.alignment.read_text()), a.tolerance)
    a.out.write_text(json.dumps(r, indent=1) + "\n")
    print(json.dumps({k: v for k, v in r.items() if k != "outside_tolerance"} | {"outside_tolerance": len(r["outside_tolerance"])}))
