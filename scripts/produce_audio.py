#!/usr/bin/env python3
"""Mix WAV -> published MP3: two-pass loudnorm (-16 LUFS, -1.5 dBTP, LRA 11, stereo), MP3 128k CBR 44.1 kHz,
ID3 chapters; writes <stem>.v<N>.mp3, chapters.json (podcast namespace 1.2.0) and manifest.json.

Chapters: optional AI-reconstruction notice at 0, then the first turn that mentions each front-matter
person (moved back to the David question that prompted it), at least --min-chapter-gap seconds apart.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from align_audio import parse_transcript  # noqa: E402
from d1_upsert import front_matter  # noqa: E402

TARGET = {"I": -16.0, "TP": -1.5, "LRA": 11.0}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def measure_loudnorm(src: Path) -> dict:
    p = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(src), "-af",
             f"loudnorm=I={TARGET['I']}:TP={TARGET['TP']}:LRA={TARGET['LRA']}:print_format=json", "-f", "null", "-"])
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
    if not m:
        raise RuntimeError("loudnorm pass 1 produced no JSON")
    return json.loads(m[-1])


def ebur128(src: Path) -> dict:
    p = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(src), "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = p.stderr[p.stderr.rfind("Summary:"):]
    get = lambda k: float(re.search(rf"^\s+{k}:\s+(-?[\d.]+|-inf)", tail, re.M).group(1).replace("-inf", "-999"))  # noqa: E731
    return {"integrated_lufs": get("I"), "lra": get("LRA"), "true_peak_dbfs": get("Peak")}


def make_chapters(md: str, timeline: dict, min_gap: float = 120.0) -> list[dict]:
    fm = front_matter(md)
    turns = parse_transcript(md)
    by_i = {t["i"]: t for t in timeline["turns"]}
    chapters = []
    if timeline.get("disclaimer") or timeline.get("recording_method") == "tts-reconstructed":
        chapters.append({"startTime": 0.0, "title": "AI-reconstructed audio notice"})
    else:
        chapters.append({"startTime": 0.0, "title": "Start"})
    hits = []
    for name in fm["people"]:
        rx = re.compile(rf"\b{re.escape(name)}\b") if " " in name else re.compile(rf"\b{re.escape(name)}\b")
        for t in turns:
            if rx.search(t.text) or (" " in name and re.search(rf"\b{re.escape(name.split()[-1])}\b", t.text)):
                i = t.index - 1 if t.speaker == "Eve" and t.index > 0 and turns[t.index - 1].speaker == "David" else t.index
                st = by_i.get(i, {}).get("edited_start")
                if st is not None:
                    hits.append((float(st), name))
                break
    for st, name in sorted(hits):
        if st - chapters[-1]["startTime"] >= min_gap:
            chapters.append({"startTime": round(st, 3), "title": name})
        elif chapters[-1]["title"] not in ("Start", "AI-reconstructed audio notice") and st - chapters[-1]["startTime"] < min_gap:
            chapters[-1]["title"] += f" & {name}"
    return chapters


def ffmetadata(title: str, chapters: list[dict], duration: float) -> str:
    esc = lambda s: re.sub(r"([=;#\\\n])", r"\\\1", s)  # noqa: E731
    out = [";FFMETADATA1", f"title={esc(title)}", "artist=Ask Eve", "album=Ask Eve"]
    for k, c in enumerate(chapters):
        end = chapters[k + 1]["startTime"] if k + 1 < len(chapters) else duration
        out += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(c['startTime'] * 1000)}", f"END={int(end * 1000)}", f"title={esc(c['title'])}"]
    return "\n".join(out) + "\n"


def produce(mix: Path, transcript: Path, timeline: dict, out_dir: Path, version: int = 1, mono: bool = False,
            min_chapter_gap: float = 120.0) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = transcript.read_text(encoding="utf-8")
    stem = transcript.stem
    fm = front_matter(md)
    dur_in = float(json.loads(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(mix)]).stdout)["format"]["duration"])
    chapters = make_chapters(md, timeline, min_chapter_gap)
    I = -19.0 if mono else TARGET["I"]
    meas = measure_loudnorm(mix)
    silent = meas["input_i"] in ("-inf", "-70.00") or float(meas["input_i"]) < -69
    meta = out_dir / "chapters.ffmeta"
    meta.write_text(ffmetadata(fm["title"], chapters, dur_in))
    mp3 = out_dir / f"{stem}.v{version}.mp3"
    if silent:   # placeholder silent audio: loudnorm cannot measure; encode as-is
        af = "aresample=44100"
    else:
        af = (f"loudnorm=I={I}:TP={TARGET['TP']}:LRA={TARGET['LRA']}:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
              f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:offset={meas['target_offset']}:linear=true,aresample=44100")
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(mix), "-i", str(meta), "-map", "0:a", "-map_metadata", "1",
         "-map_chapters", "1", "-af", af, "-ar", "44100", "-ac", "1" if mono else "2", "-c:a", "libmp3lame", "-b:a", "128k",
         "-id3v2_version", "3", str(mp3)])
    probe = json.loads(run(["ffprobe", "-v", "error", "-show_chapters", "-show_entries", "format=duration,size", "-of", "json", str(mp3)]).stdout)
    loud = ebur128(mp3)
    data_sha = hashlib.sha256()
    with open(mp3, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            data_sha.update(chunk)
    chapters_json = {"version": "1.2.0", "chapters": chapters}
    (out_dir / "chapters.json").write_text(json.dumps(chapters_json, indent=1) + "\n")
    method = timeline.get("recording_method", "mic-plus-tts")
    manifest = {
        "stem": stem, "version": version, "audio_key": f"audio/en/{stem}.v{version}.mp3",
        "audio_url": f"https://audio.askevepodcast.com/audio/en/{stem}.v{version}.mp3",
        "audio_bytes": int(probe["format"]["size"]), "audio_sha256": data_sha.hexdigest(), "audio_mime": "audio/mpeg",
        "duration_seconds": round(float(probe["format"]["duration"])), "loudness_lufs": loud["integrated_lufs"],
        "true_peak_dbfs": loud["true_peak_dbfs"], "id3_chapters": len(probe.get("chapters", [])), "chapters": chapters,
        "recording_method": method, "ai_reconstructed": method == "tts-reconstructed",
        "placeholder_audio": bool(timeline.get("placeholder_audio", True)),
        "tts": timeline.get("tts"), "narrator": timeline.get("narrator"),
        "missing_turns": timeline.get("missing_turns", []), "transcript_sha256": hashlib.sha256(md.encode()).hexdigest(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mix", required=True, type=Path)
    ap.add_argument("--transcript", required=True, type=Path)
    ap.add_argument("--timeline", required=True, type=Path, help="revoice.json from revoice_eve.py")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--version", type=int, default=1)
    ap.add_argument("--mono", action="store_true")
    ap.add_argument("--min-chapter-gap", type=float, default=120.0)
    a = ap.parse_args(argv)
    m = produce(a.mix, a.transcript, json.loads(a.timeline.read_text()), a.out, a.version, a.mono, a.min_chapter_gap)
    print(json.dumps({k: m[k] for k in ("audio_key", "audio_bytes", "duration_seconds", "loudness_lufs", "id3_chapters", "placeholder_audio")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
