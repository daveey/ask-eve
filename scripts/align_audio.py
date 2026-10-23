#!/usr/bin/env python3
"""Align a verbatim transcript to a David-only phone recording.

The phone screen recording captures David (AirPods mic) but NOT Eve (10:34 UTC test). So:
  * David turns are found in the audio: ASR word timestamps + fuzzy match of each turn's text.
  * Eve turns are the silent gaps between David turns: start = end of preceding David turn + lead,
    end = start of next David turn - tail, consecutive Eve turns split the gap by createTime deltas.
  * Unmatched David turns are interpolated from neighbouring anchors using share-JSON createTime.
The transcript text is never changed; ASR output is only used for timing.

ASR is injectable: --asr-words <json> (list of {w,start,end}) or the faster-whisper backend.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import re
import sys
import wave
from pathlib import Path
from typing import Callable, Iterable, Protocol

from rapidfuzz import fuzz

TURN_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] (David|Eve): ?(.*)$")
SYNC_PHRASE = "ask eve take one"


@dataclasses.dataclass
class Word:
    w: str
    start: float
    end: float


@dataclasses.dataclass
class Turn:
    index: int
    speaker: str
    created_at: str          # ISO-8601 UTC
    t: float                 # seconds since first turn's createTime
    text: str
    meta: str = ""           # "> ..." lines under the turn (e.g. "relayed as:"); never spoken, never matched


class ASRBackend(Protocol):
    name: str

    def transcribe(self, audio_path: Path) -> list[Word]: ...


class JsonWords:
    """Precomputed/fake ASR words: [{"w": "...", "start": 1.0, "end": 1.3}, ...]."""

    def __init__(self, path_or_words):
        self.name = "json-words"
        self._src = path_or_words

    def transcribe(self, audio_path: Path | None = None) -> list[Word]:
        data = self._src
        if isinstance(data, (str, Path)):
            data = json.loads(Path(data).read_text())
            if isinstance(data, dict):
                data = data["words"]
        return [Word(str(d["w"]), float(d["start"]), float(d["end"])) for d in data]


class FasterWhisper:
    """faster-whisper backend (CPU int8). Audio must be 16 kHz mono 16-bit WAV (see ingest.yml)."""

    def __init__(self, model: str = "small", compute_type: str = "int8", threads: int = 0, language: str = "en"):
        self.model_name, self.compute_type, self.threads, self.language = model, compute_type, threads, language
        self.name = f"faster-whisper {model} {compute_type}"

    def transcribe(self, audio_path: Path) -> list[Word]:
        import numpy as np
        from faster_whisper import WhisperModel

        with wave.open(str(audio_path), "rb") as w:
            if w.getnchannels() != 1 or w.getframerate() != 16000 or w.getsampwidth() != 2:
                raise ValueError("ASR input must be 16 kHz mono s16 WAV")
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        model = WhisperModel(self.model_name, device="cpu", compute_type=self.compute_type, cpu_threads=self.threads)
        segments, _ = model.transcribe(audio, language=self.language, word_timestamps=True, vad_filter=True, beam_size=1)
        return [Word(wd.word.strip(), float(wd.start), float(wd.end)) for seg in segments for wd in (seg.words or [])]


# ---------------------------------------------------------------- parsing

def norm(s: str) -> str:
    s = s.lower().replace("’", "'")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_transcript(md: str, share: dict | None = None) -> list[Turn]:
    """Turns from an episodes/*.md file (multi-paragraph turns supported).

    createTime comes from the share JSON when given (full precision), else from [HH:MM:SS] plus the
    front-matter date (rolling over midnight when the clock goes backwards).
    """
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    date = None
    if m:
        dm = re.search(r"^date: (\d{4}-\d{2}-\d{2})$", m.group(1), re.M)
        date = dm.group(1) if dm else None
        body = md[m.end():]
    else:
        body = md
    raw: list[list] = []
    for line in body.split("\n"):
        tm = TURN_RE.match(line)
        if tm:
            raw.append([tm.group(4), (int(tm.group(1)), int(tm.group(2)), int(tm.group(3))), tm.group(5), ""])
        elif raw and line.startswith(">"):
            raw[-1][3] += line[1:].strip() + "\n"   # metadata block, not part of the spoken turn
        elif raw and line.strip():
            raw[-1][2] += "\n" + line
    if share is not None:
        rs = sorted(share["responses"], key=lambda x: x["createTime"])
        if len(rs) != len(raw):
            raise ValueError(f"share JSON has {len(rs)} turns, transcript has {len(raw)}")
        stamps = [_parse_iso(r["createTime"]) for r in rs]
    else:
        if not date:
            raise ValueError("transcript has no front-matter date and no share JSON was given")
        day = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc)
        stamps, prev = [], None
        for _, (h, mi, s), _, _ in raw:
            ts = day.replace(hour=h, minute=mi, second=s)
            while prev is not None and ts < prev - dt.timedelta(hours=12):
                ts += dt.timedelta(days=1)
            stamps.append(ts)
            prev = ts
    t0 = stamps[0]
    return [Turn(i, sp, _iso(st), (st - t0).total_seconds(), txt.strip(), meta.strip())
            for i, ((sp, _, txt, meta), st) in enumerate(zip(raw, stamps))]


# ---------------------------------------------------------------- matching

def _char_to_word(offsets: list[int], pos: int) -> int:
    lo, hi = 0, len(offsets) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if offsets[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def find_phrase(qwords: list[str], words: list[str], lo: int, hi: int, min_score: float) -> tuple[int, int, float] | None:
    """Best fuzzy location of qwords inside words[lo:hi] -> (first_idx, last_idx, score)."""
    hi = min(hi, len(words))
    if lo >= hi or not qwords:
        return None
    window = words[lo:hi]
    offsets, pos = [], 0
    for w in window:
        offsets.append(pos)
        pos += len(w) + 1
    hay = " ".join(window)
    q = " ".join(qwords)
    al = fuzz.partial_ratio_alignment(q, hay, score_cutoff=min_score)
    if al is None:
        return None
    first = lo + _char_to_word(offsets, al.dest_start)
    last = lo + _char_to_word(offsets, max(al.dest_start, al.dest_end - 1))
    # refine: snap start to the first exact word of the query found near the match start
    for k in range(first, min(first + 3, last + 1)):
        if words[k] in qwords[:3]:
            first = k
            break
    return first, last, float(al.score)


@dataclasses.dataclass
class Params:
    query_words: int = 25
    min_chars: int = 12
    min_score: float = 70.0
    window_slack: int = 150
    window_factor: float = 1.6
    window_cap: int = 3000
    long_turn_words: int = 15
    pass1_turns: int = 40
    pass1_score: float = 85.0
    time_window: float = 90.0
    eve_lead: float = 0.3       # silence after David stops before Eve's placement starts
    eve_tail: float = 0.3       # silence kept before the next David turn
    min_gap: float = 0.2


def align(turns: list[Turn], words: list[Word], params: Params = Params(), sync_phrase: str | None = SYNC_PHRASE,
          anchor_offset: float | None = None, match_speakers: tuple[str, ...] = ("David",)) -> dict:
    """Return alignment dict (see module doc). anchor_offset: prior raw time of turn 0 (creationdate).

    match_speakers: speakers searched in the audio. Default ('David',) for David-only phone audio; the
    synthetic-episode verification pass uses ('David', 'Eve') because both voices are in the mix."""
    W = [norm(w.w) for w in words]
    keep = [i for i, w in enumerate(W) if w]
    W = [W[i] for i in keep]
    words = [words[i] for i in keep]

    # 1. sync phrase among the first ~300 words
    sync = None
    if sync_phrase and W:
        hit = find_phrase(norm(sync_phrase).split(), W, 0, 300, 80)
        if hit:
            sync = {"raw_seconds": words[hit[0]].start, "score": hit[2], "word_index": hit[0]}

    david = [t for t in turns if t.speaker in match_speakers]
    starts = [w.start for w in words]
    res: dict[int, dict] = {}

    def idx_at(sec: float) -> int:
        import bisect
        return bisect.bisect_left(starts, sec)

    # pass 1: coarse clock offset (raw ≈ createTime + c) from long, distinctive David turns, global search
    long_turns = sorted((t for t in david if len(t.text.split()) >= params.long_turn_words),
                        key=lambda t: -len(t.text.split()))[: params.pass1_turns]
    c_hits = []
    for t in long_turns:
        hit = find_phrase(norm(t.text).split()[: params.query_words], W, 0, len(W), params.pass1_score)
        if hit:
            c_hits.append(words[hit[0]].start - t.t)
    if c_hits:
        c_hits.sort()
        c0 = c_hits[len(c_hits) // 2]
    elif anchor_offset is not None:
        c0 = anchor_offset
    else:
        c0 = None   # no clock prior: fall back to a sequential cursor search

    # pass 2: every David turn, searched in a time window around createTime + c (never before the last match)
    cursor, words_since, matchable = 0, 0, 0
    for t in david:
        tw = norm(t.text).split()
        n = len(tw)
        if len(" ".join(tw[: params.query_words])) < params.min_chars:
            words_since += n
            continue
        matchable += 1
        if c0 is not None:
            est_dur = n / 2.6
            lo = max(cursor, idx_at(t.t + c0 - params.time_window - est_dur))
            hi = idx_at(t.t + c0 + params.time_window) + 1
        else:
            lo = cursor
            hi = cursor + min(params.window_cap, int(params.window_factor * (words_since + n)) + params.window_slack)
        head = find_phrase(tw[: params.query_words], W, lo, hi, params.min_score)
        if not head:
            words_since += n
            continue
        first = head[0]
        tail = find_phrase(tw[-params.query_words:], W, first, first + int(1.5 * n) + 30, params.min_score) if n > params.query_words else None
        last = tail[1] if tail else min(len(W) - 1, max(head[1], first + n - 1))
        res[t.index] = {"raw_start": words[first].start, "raw_end": words[last].end, "method": "asr-match",
                        "score": round(head[2], 1)}
        cursor, words_since = last + 1, 0

    # sync phrase pins turn 0 if it is David's
    if sync and turns and turns[0].speaker in match_speakers:
        r0 = res.get(0)
        if r0 is None or abs(r0["raw_start"] - sync["raw_seconds"]) < 5:
            end0 = r0["raw_end"] if r0 else sync["raw_seconds"] + 2.0
            res[0] = {"raw_start": sync["raw_seconds"], "raw_end": end0, "method": "sync-phrase", "score": round(sync["score"], 1)}

    # monotonic cleanup: drop anchors that go backwards
    last_end = -1.0
    for t in david:
        r = res.get(t.index)
        if r and r["raw_start"] < last_end - 0.05:
            del res[t.index]
        elif r:
            last_end = r["raw_end"]

    anchors = [(t, res[t.index]) for t in david if t.index in res]
    # global offset raw ≈ t + c (real time), median over anchors; fallback to creationdate prior / 0
    if anchors:
        diffs = sorted(r["raw_start"] - t.t for t, r in anchors)
        c = diffs[len(diffs) // 2]
    else:
        c = anchor_offset if anchor_offset is not None else 0.0
    slope = None
    if len(anchors) >= 3:
        xs = [t.t for t, _ in anchors]
        ys = [r["raw_start"] for _, r in anchors]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        if vx > 0:
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx

    def interp(t: Turn) -> float:
        prev = next(((pt, pr) for pt, pr in reversed(anchors) if pt.index < t.index), None)
        nxt = next(((nt, nr) for nt, nr in anchors if nt.index > t.index), None)
        if prev and nxt and nt_t(nxt) > nt_t(prev):
            (pt, pr), (nt, nr) = prev, nxt
            f = (t.t - pt.t) / (nt.t - pt.t)
            return pr["raw_start"] + f * (nr["raw_start"] - pr["raw_start"])
        if prev:
            return prev[1]["raw_start"] + (t.t - prev[0].t)
        if nxt:
            return nxt[1]["raw_start"] - (nxt[0].t - t.t)
        return t.t + c

    def nt_t(a):
        return a[0].t

    # 2. unmatched David turns: interpolated start, estimated end by word count (2.6 words/s)
    for t in david:
        if t.index not in res:
            s = interp(t)
            res[t.index] = {"raw_start": s, "raw_end": s + max(0.5, len(t.text.split()) / 2.6), "method": "interpolated", "score": None}
    # keep David turns ordered and non-overlapping
    prev_end = 0.0
    for t in david:
        r = res[t.index]
        if r["raw_start"] < prev_end:
            r["raw_start"] = prev_end
        if r["raw_end"] < r["raw_start"]:
            r["raw_end"] = r["raw_start"]
        prev_end = r["raw_end"]

    # 3. Eve turns: gaps between David turns
    i = 0
    n = len(turns)
    while i < n:
        if turns[i].speaker in match_speakers:
            i += 1
            continue
        j = i
        while j < n and turns[j].speaker not in match_speakers:
            j += 1
        block = turns[i:j]
        prev_d = turns[i - 1] if i > 0 else None
        next_d = turns[j] if j < n else None
        gs = (res[prev_d.index]["raw_end"] + params.eve_lead) if prev_d else max(0.0, block[0].t + c)
        if next_d:
            ge = res[next_d.index]["raw_start"] - params.eve_tail
        else:
            ge = gs + sum(max(1.0, len(b.text.split()) / 2.6) for b in block)
        if ge - gs < params.min_gap:
            # (almost) no silence between the David turns: zero-length slot in the middle of whatever gap
            # exists; revoice_eve.py inserts the time Eve's clip needs there, so nothing overlaps
            lo_ = res[prev_d.index]["raw_end"] if prev_d else gs
            hi_ = res[next_d.index]["raw_start"] if next_d else ge
            gs = ge = lo_ + max(0.0, hi_ - lo_) / 2
        # split gap across consecutive Eve turns by createTime deltas (equal split if deltas are zero)
        bounds = [b.t for b in block] + [next_d.t if next_d else block[-1].t + 1.0]
        total = bounds[-1] - bounds[0]
        for k, b in enumerate(block):
            f0 = (bounds[k] - bounds[0]) / total if total > 0 else k / len(block)
            f1 = (bounds[k + 1] - bounds[0]) / total if total > 0 else (k + 1) / len(block)
            res[b.index] = {"raw_start": gs + f0 * (ge - gs), "raw_end": gs + f1 * (ge - gs), "method": "gap-placed",
                            "score": None, "gap_seconds": round(ge - gs, 3)}
        i = j

    matched = sum(1 for t in david if res[t.index]["method"] in ("asr-match", "sync-phrase"))
    coverage = matched / matchable if matchable else 0.0
    out_turns = []
    for t in turns:
        r = res[t.index]
        out_turns.append({"i": t.index, "speaker": t.speaker, "created_at": t.created_at,
                          "raw_start": round(r["raw_start"], 3), "raw_end": round(r["raw_end"], 3),
                          "method": r["method"], "score": r["score"],
                          **({"gap_seconds": r["gap_seconds"]} if "gap_seconds" in r else {})})
    return {
        "alignment_method": "asr-david-gaps" if anchors else ("creationdate-only" if anchor_offset is not None else "linear-fit"),
        "alignment_coverage": round(coverage, 4),
        "david_turns": len(david), "david_matchable": matchable, "david_matched": matched,
        "eve_turns": sum(1 for t in turns if t.speaker == "Eve"), "match_speakers": list(match_speakers),
        "sync_offset_seconds": round(res[0]["raw_start"], 3) if turns else None,
        "sync_phrase": sync,
        "fit": {"offset_seconds": round(c, 3), "slope": round(slope, 5) if slope is not None else None},
        "turns": out_turns,
    }


# ---------------------------------------------------------------- anchors from the phone file

def creationdate_utc(media: Path) -> str | None:
    """com.apple.quicktime.creationdate (or creation_time) from ffprobe, as UTC ISO."""
    import subprocess

    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(media)],
                         capture_output=True, text=True, check=True).stdout
    tags = json.loads(out).get("format", {}).get("tags", {})
    v = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    if not v:
        return None
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v.replace("Z", "+00:00"))
    return _iso(dt.datetime.fromisoformat(v))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None, backend_factory: Callable[[argparse.Namespace], ASRBackend] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", required=True, type=Path)
    ap.add_argument("--audio", type=Path, help="16 kHz mono WAV of the David-only recording")
    ap.add_argument("--share-json", type=Path, help="full-precision createTime source")
    ap.add_argument("--asr-words", type=Path, help="use precomputed words JSON instead of running ASR")
    ap.add_argument("--save-words", type=Path, help="write ASR words JSON here")
    ap.add_argument("--anchor-from", type=Path, help="phone recording; reads com.apple.quicktime.creationdate")
    ap.add_argument("--model", default="small")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--sync-phrase", default=SYNC_PHRASE)
    ap.add_argument("--min-coverage", type=float, default=0.5)
    ap.add_argument("--allow-low-coverage", action="store_true")
    ap.add_argument("--match-speakers", default="David", help="comma list; 'David,Eve' to verify a synthetic mix")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)

    md = a.transcript.read_text(encoding="utf-8")
    share = json.loads(a.share_json.read_text()) if a.share_json else None
    turns = parse_transcript(md, share)
    if backend_factory:
        backend = backend_factory(a)
    elif a.asr_words:
        backend = JsonWords(a.asr_words)
    else:
        if not a.audio:
            ap.error("--audio or --asr-words is required")
        backend = FasterWhisper(a.model, a.compute_type)
    words = backend.transcribe(a.audio)
    if a.save_words:
        a.save_words.write_text(json.dumps([dataclasses.asdict(w) for w in words]))
    rec_start = creationdate_utc(a.anchor_from) if a.anchor_from else None
    prior = (_parse_iso(turns[0].created_at) - _parse_iso(rec_start)).total_seconds() if rec_start and turns else None
    result = align(turns, words, sync_phrase=a.sync_phrase or None, anchor_offset=prior,
                   match_speakers=tuple(x.strip() for x in a.match_speakers.split(",") if x.strip()))
    result.update({
        "transcript": str(a.transcript), "transcript_sha256": hashlib.sha256(md.encode()).hexdigest(),
        "asr_model": backend.name, "asr_words": len(words), "recording_started_at": rec_start,
        "creationdate_prior_offset": prior,
        "raw_audio_sha256": sha256_file(a.anchor_from) if a.anchor_from else None,
    })
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=1) + "\n")
    summary = {k: result[k] for k in ("alignment_method", "alignment_coverage", "david_turns", "david_matched", "eve_turns", "sync_offset_seconds")}
    print(json.dumps(summary))
    if result["alignment_coverage"] < a.min_coverage and not a.allow_low_coverage:
        print(f"error: coverage {result['alignment_coverage']} < {a.min_coverage}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
