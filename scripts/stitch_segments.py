#!/usr/bin/env python3
"""Stitch several phone recordings (one session split across segments) into one David track.

Order and placement: by com.apple.quicktime.creationdate (else creation_time, else argument order).
Each segment is placed at (its start - first start) seconds; gaps become silence so share-JSON
createTime deltas still line up. Overlaps (coarse clocks) and missing dates fall back to back-to-back
placement with --gap seconds. Outputs 48 kHz mono s16 (mixing) + 16 kHz mono (ASR) + segments.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from align_audio import creationdate_utc, sha256_file  # noqa: E402

SR = 48000
# docs/audio-pipeline.md §3.0. Voice Memos + AirPods arrives already noise-suppressed (floor ~-75 dBFS on
# 2026-10-23), so the default skips afftdn; use --clean-chain "highpass=f=80,afftdn=nr=12:nf=-40:tn=1,..." for noisy walks.
DEFAULT_CHAIN = "highpass=f=80,acompressor=threshold=-21dB:ratio=3:attack=10:release=200:makeup=3"


def to_wav(src: Path, dst: Path, sr: int) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-map", "0:a:0", "-ac", "1", "-ar", str(sr),
                    "-c:a", "pcm_s16le", str(dst)], check=True)


def stitch(segments: list[Path], out_dir: Path, gap: float = 2.0, max_gap: float = 3600.0, chain: str | None = DEFAULT_CHAIN) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    info = []
    for k, seg in enumerate(segments):
        w = out_dir / f"seg{k:02d}.wav"
        to_wav(seg, w, SR)
        with wave.open(str(w), "rb") as r:
            dur = r.getnframes() / SR
        try:
            cd = creationdate_utc(seg)
        except subprocess.CalledProcessError:
            cd = None
        info.append({"arg_index": k, "path": str(seg), "wav": str(w), "duration": round(dur, 3), "creationdate": cd,
                     "sha256": sha256_file(seg), "bytes": seg.stat().st_size})
    dated = all(i["creationdate"] for i in info)
    if dated:
        info.sort(key=lambda i: i["creationdate"])
    t0 = dt.datetime.fromisoformat(info[0]["creationdate"].replace("Z", "+00:00")) if dated else None
    cur = 0.0
    for i in info:
        if dated:
            want = (dt.datetime.fromisoformat(i["creationdate"].replace("Z", "+00:00")) - t0).total_seconds()
            if want < cur or want - cur > max_gap:
                want = cur + (gap if cur else 0.0)
                i["placement"] = "sequential-fallback"
            else:
                i["placement"] = "creationdate"
        else:
            want = cur + (gap if cur else 0.0)
            i["placement"] = "sequential"
        i["offset"] = round(want, 3)
        cur = want + i["duration"]
    raw48 = out_dir / "david_raw_48k.wav"
    with wave.open(str(raw48), "wb") as o:
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR)
        written = 0
        for i in info:
            target = int(round(i["offset"] * SR))
            o.writeframes(b"\x00\x00" * max(0, target - written)); written = max(written, target)
            with wave.open(i["wav"], "rb") as r:
                n = r.getnframes(); o.writeframes(r.readframes(n)); written += n
    for i in info:
        Path(i["wav"]).unlink()
        del i["wav"]
    out16 = out_dir / "david_16k.wav"          # ASR input: uncleaned
    to_wav(raw48, out16, 16000)
    out48 = out_dir / "david_48k.wav"          # mixing input: cleaned (same length, sample-aligned)
    if chain:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(raw48), "-af", chain, "-ar", str(SR), "-ac", "1",
                        "-c:a", "pcm_s16le", str(out48)], check=True)
    else:
        raw48.replace(out48)
    gaps = [{"after_segment": k, "start": round(a["offset"] + a["duration"], 3), "end": b["offset"],
             "seconds": round(b["offset"] - a["offset"] - a["duration"], 3)}
            for k, (a, b) in enumerate(zip(info, info[1:])) if b["offset"] - a["offset"] - a["duration"] > 0.5]
    res = {"segments": info, "recording_started_at": info[0]["creationdate"], "duration": round(cur, 3),
           "recording_gaps": gaps, "processing_chain": chain or "none",
           "files": {"david_48k": str(out48), "david_16k": str(out16)}}
    (out_dir / "segments.json").write_text(json.dumps(res, indent=1) + "\n")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("segments", nargs="+", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--gap", type=float, default=2.0)
    ap.add_argument("--clean-chain", default=DEFAULT_CHAIN, help='ffmpeg -af chain for the mixing track; "" = none')
    a = ap.parse_args(argv)
    r = stitch(a.segments, a.out, a.gap, chain=a.clean_chain or None)
    print(json.dumps({"segments": len(r["segments"]), "duration": r["duration"], "placement": [s["placement"] for s in r["segments"]],
                      "recording_gaps": r["recording_gaps"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
