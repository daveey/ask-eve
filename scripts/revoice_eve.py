#!/usr/bin/env python3
"""Re-voice Eve's turns with xAI TTS and mix them into the David-only phone track.

Inputs: the alignment JSON from align_audio.py (raw_start/raw_end per turn; Eve turns are
'gap-placed' slots in the silence between David turns), the transcript, and David's audio.

Placement rules (docs/audio-pipeline.md §11.4):
  * each Eve clip starts at its slot start (end of the preceding David turn + lead);
  * clip longer than its slot  -> insert silence at the slot start so the clip plus `pause_after`
    fits; everything after shifts later (never overlap David);
  * clip shorter than its slot -> pad with the remaining natural pause (optionally capped by
    --max-pause, which cuts surplus silence only between ASR-matched David turns);
  * the resulting time map (raw -> edited segments) is written so chapters/turn offsets stay right.

TTS: POST https://api.x.ai/v1/tts (voice_id 'eve'), clips cached by content hash. Tests inject a
fake client; nothing here runs without XAI_API_KEY unless a client is injected.
"""
from __future__ import annotations

import argparse
import array
import bisect
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Protocol

SR = 48000
TTS_URL = "https://api.x.ai/v1/tts"
TTS_MAX_CHARS = 60000   # docs.x.ai text-to-speech: REST text limit 60,000 characters (verified 2026-10-23)
sys.path.insert(0, str(Path(__file__).resolve().parent))


class TTSClient(Protocol):
    name: str

    def synthesize(self, text: str) -> tuple[bytes, str]:
        """Return (audio bytes, file extension)."""


class XAITTS:
    """xAI TTS REST client. Request/response shape per docs.x.ai (2026-10-23): JSON body
    {text, voice_id, language, output_format{codec,sample_rate}}; response body = raw audio bytes."""

    placeholder = False

    def __init__(self, api_key: str, voice_id: str = "eve", language: str = "en", speed: float | None = None,
                 url: str = TTS_URL, retries: int = 5, timeout: float = 900):
        if not api_key:
            raise ValueError("XAI_API_KEY is empty")
        self.api_key, self.voice_id, self.language, self.speed = api_key, voice_id, language, speed
        self.url, self.retries, self.timeout = url, retries, timeout
        self.name = f"xai_tts voice={voice_id}"

    def payload(self, text: str) -> dict:
        p = {"text": text, "voice_id": self.voice_id, "language": self.language,
             "output_format": {"codec": "wav", "sample_rate": SR}}
        if self.speed is not None:
            p["speed"] = self.speed
        return p

    def synthesize(self, text: str) -> tuple[bytes, str]:
        body = json.dumps(self.payload(text)).encode()
        delay = 2.0
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.url, data=body, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 (fixed https host)
                    return r.read(), "wav"
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 503) and attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                detail = e.read()[:300].decode("utf-8", "replace")
                raise RuntimeError(f"xAI TTS HTTP {e.code}: {detail}") from None
        raise RuntimeError("unreachable")


DEFAULT_EVE_VOICE = "eve"      # xAI built-in: "Energetic and upbeat, British accent" (docs.x.ai voices table, 2026-10-23)
DEFAULT_NARRATOR_VOICE = "rex" # xAI built-in: "Confident and clear"; a different speaker from Eve, for David's lines
DISCLAIMER = "Voices in this episode are AI-reconstructed from the text transcript."
HYBRID_DISCLAIMER = ("Eve's voice, and David's lines wherever his recording is missing, are AI-reconstructed "
                     "from the text transcript. David's real voice is used only where his recording matches the transcript.")
HYBRID_DISCLOSURE = ("AI-reconstructed audio: Eve's lines are xAI text-to-speech ('eve' voice) from the verbatim chat transcript; "
                     "David's lines are his own recorded voice only where the recording matches the transcript, and an AI narrator "
                     "voice ('rex') everywhere else. Transcript gaps are marked, not invented.")
FLAG_DISCLAIMER = ("Eve's voice in this episode is AI-reconstructed from the text transcript. David's voice is his own recording. "
                   "Where his recording is missing, his line is flagged in the transcript and chapters and is not voiced.")
FLAG_DISCLOSURE = ("AI-reconstructed audio: Eve's lines are xAI text-to-speech ('eve' voice) from the verbatim transcript; "
                   "David's lines are his own recorded voice where the recording matches the transcript. David's lines with no "
                   "matching recording are flagged as missing audio and are not voiced (no narrator fill). Transcript gaps are marked, not invented.")
GAP_RE = re.compile(r"^\[transcript gap\b", re.I)   # marker turns: never voiced


class SilentTTS:
    """PLACEHOLDER backend: silence with a speech-like duration (~14 chars/s). Exercises every stage offline."""

    placeholder = True

    def __init__(self, voice: str, chars_per_second: float = 14.0):
        self.voice, self.cps = voice, chars_per_second
        self.name = f"placeholder-silent voice={voice}"

    def synthesize(self, text: str) -> tuple[bytes, str]:
        import io
        n = int(max(0.4, len(text) / self.cps) * SR)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(b"\x00\x00" * n)
        return buf.getvalue(), "wav"


class PiperTTS:
    """PLACEHOLDER backend: local offline Piper voice (robotic, not Eve). Lets the alignment pass check
    placement on real speech when no XAI_API_KEY is available. Never published."""

    placeholder = True

    def __init__(self, voice: str, model: str, piper_bin: str = "piper"):
        self.voice, self.model, self.bin = voice, model, piper_bin
        self.name = f"placeholder-piper voice={voice} model={Path(model).stem}"

    def synthesize(self, text: str) -> tuple[bytes, str]:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.wav"
            subprocess.run([self.bin, "-m", self.model, "-f", str(out)], input=text.encode(), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return out.read_bytes(), "wav"


def make_client(role: str, backend: str | None = None):
    """role 'eve' or 'narrator'. backend: 'xai' | 'piper' | 'silent' | None (auto: xai if XAI_API_KEY else TTS_BACKEND or silent).
    Voices: EVE_VOICE (default 'eve'), NARRATOR_VOICE (default 'rex')."""
    voice = os.environ.get("EVE_VOICE", DEFAULT_EVE_VOICE) if role == "eve" else os.environ.get("NARRATOR_VOICE", DEFAULT_NARRATOR_VOICE)
    if role == "narrator" and voice.lower() == os.environ.get("EVE_VOICE", DEFAULT_EVE_VOICE).lower():
        raise ValueError("NARRATOR_VOICE must differ from EVE_VOICE")
    backend = backend or ("xai" if os.environ.get("XAI_API_KEY") else os.environ.get("TTS_BACKEND", "silent"))
    if backend == "xai":
        return XAITTS(os.environ.get("XAI_API_KEY", ""), voice_id=voice)
    if backend == "piper":
        model = os.environ["PIPER_EVE_MODEL" if role == "eve" else "PIPER_NARRATOR_MODEL"]
        return PiperTTS(voice, model, os.environ.get("PIPER_BIN", "piper"))
    if backend == "silent":
        return SilentTTS(voice)
    raise ValueError(f"unknown TTS backend {backend!r}")


# ---------------------------------------------------------------- text

def tts_text(text: str) -> str:
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)      # markdown links -> text
    t = re.sub(r"[*_#`>]+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def split_text(text: str, limit: int = TTS_MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        while len(sent) > limit:
            parts.append(sent[:limit]); sent = sent[limit:]
        if len(cur) + len(sent) + 1 > limit:
            parts.append(cur); cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    return parts


# ---------------------------------------------------------------- audio helpers

def to_wav48(src: Path, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", str(SR),
                    "-c:a", "pcm_s16le", str(dst)], check=True)


def wav_seconds(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / w.getframerate()


def concat_wavs(parts: list[Path], dst: Path) -> None:
    with wave.open(str(dst), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(SR)
        for p in parts:
            with wave.open(str(p), "rb") as w:
                out.writeframes(w.readframes(w.getnframes()))


def make_clip(client: TTSClient, text: str, cache: Path) -> Path:
    clean = tts_text(text)
    key = hashlib.sha256(json.dumps([client.name, clean]).encode()).hexdigest()[:20]
    final = cache / f"{key}.wav"
    if final.exists():
        return final
    cache.mkdir(parents=True, exist_ok=True)
    pieces = []
    for k, chunk in enumerate(split_text(clean)):
        data, ext = client.synthesize(chunk)
        raw = cache / f"{key}.{k}.raw.{ext}"
        raw.write_bytes(data)
        norm = cache / f"{key}.{k}.wav"
        to_wav48(raw, norm)
        raw.unlink()
        pieces.append(norm)
    if len(pieces) == 1:
        pieces[0].rename(final)
    else:
        concat_wavs(pieces, final)
        for p in pieces:
            p.unlink()
    return final


# ---------------------------------------------------------------- time map

@dataclasses.dataclass
class Segment:
    raw_start: float
    raw_end: float
    edited_start: float


class TimeMap:
    """Piecewise-linear raw->edited map. Inserted silence = jump in edited time; cut = jump in raw time."""

    def __init__(self, segments: list[Segment]):
        self.segments = segments
        self._starts = [s.raw_start for s in segments]

    def to_edited(self, t: float, side: str = "right") -> float:
        """side='left' maps an insertion point to the start of the inserted silence."""
        segs = self.segments
        if not segs:
            return t
        i = max(0, (bisect.bisect_left if side == "left" else bisect.bisect_right)(self._starts, t) - 1)
        s = segs[i]
        if t <= s.raw_end or i == len(segs) - 1:
            return s.edited_start + (t - s.raw_start)
        return segs[i + 1].edited_start          # inside a cut: collapse to the cut point

    def as_json(self) -> list[dict]:
        return [{"raw_start": round(s.raw_start, 4), "raw_end": round(s.raw_end, 4), "edited_start": round(s.edited_start, 4)} for s in self.segments]


def build_ops(turns: list[dict], clip_secs: dict[int, float], pause_after: float, max_pause: float | None):
    """Return ops [(raw_point, 'insert', seconds) | (raw_a, 'cut', raw_b)] and Eve placements.

    Placements are (raw_point, offset): several Eve turns can share one raw point (zero-length slots around an
    interpolated David turn), and each one then starts `offset` seconds into the silence inserted there, after
    the clips queued before it at the same point, so they never overlap."""
    ops, place = [], {}
    queued: dict[float, float] = {}
    by_i = {t["i"]: t for t in turns}
    for t in turns:
        if t["speaker"] != "Eve" or t["i"] not in clip_secs:
            continue
        s, e = t["raw_start"], t["raw_end"]
        slot = max(0.0, e - s)
        need = clip_secs[t["i"]] + pause_after
        place[t["i"]] = (s, queued.get(s, 0.0))
        if need > slot:
            ops.append((s, "insert", need - slot))
            queued[s] = queued.get(s, 0.0) + need - slot
        elif max_pause is not None and slot - clip_secs[t["i"]] > max_pause:
            prev_d, next_d = by_i.get(t["i"] - 1), by_i.get(t["i"] + 1)
            safe = all(x is None or x["speaker"] == "Eve" or x["method"] in ("asr-match", "sync-phrase") for x in (prev_d, next_d))
            if safe:
                ops.append((s + clip_secs[t["i"]] + max_pause, "cut", e))
    ops.sort(key=lambda o: o[0])
    return ops, place


def build_timemap(ops, total_raw: float) -> TimeMap:
    segs, cur_raw, cur_ed = [], 0.0, 0.0
    for p, kind, x in ops:
        if p < cur_raw:
            p = cur_raw
        segs.append(Segment(cur_raw, p, cur_ed))
        cur_ed += p - cur_raw
        if kind == "insert":
            cur_ed += x
            cur_raw = p
        else:
            cur_raw = max(p, x)
    segs.append(Segment(cur_raw, max(cur_raw, total_raw), cur_ed))
    return TimeMap([s for s in segs if s.raw_end > s.raw_start or s is segs[-1]])


# ---------------------------------------------------------------- render

def _frames(sec: float) -> int:
    return int(round(sec * SR))


def render_tracks(david_wav: Path, tm: TimeMap, eve_clips: list[tuple[float, Path]], out_david: Path, out_eve: Path) -> float:
    """Sample-exact edited David track (segments + silences) and Eve track (clips at edited times)."""
    with wave.open(str(david_wav), "rb") as d, wave.open(str(out_david), "wb") as o:
        if (d.getnchannels(), d.getframerate(), d.getsampwidth()) != (1, SR, 2):
            raise ValueError("David track must be 48 kHz mono s16 WAV")
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR)
        written = 0
        for seg in tm.segments:
            target = _frames(seg.edited_start)
            if target > written:
                o.writeframes(b"\x00\x00" * (target - written)); written = target
            a, b = _frames(seg.raw_start), _frames(seg.raw_end)
            d.setpos(min(a, d.getnframes()))
            n = max(0, min(b, d.getnframes()) - a)
            o.writeframes(d.readframes(n)); written += n
        total = written
    with wave.open(str(out_eve), "wb") as o:
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR)
        written = 0
        for start, clip in sorted(eve_clips, key=lambda x: x[0]):
            target = _frames(start)
            if target < written:
                raise ValueError(f"Eve clip at {start:.3f}s overlaps the previous clip")
            o.writeframes(b"\x00\x00" * (target - written)); written = target
            with wave.open(str(clip), "rb") as c:
                n = c.getnframes(); o.writeframes(c.readframes(n)); written += n
        if written < total:
            o.writeframes(b"\x00\x00" * (total - written)); written = total
    return max(total, written) / SR


def mix(david: Path, eve: Path, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(david), "-i", str(eve), "-filter_complex",
                    "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[m]", "-map", "[m]",
                    "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(dst)], check=True)


def revoice(alignment: dict, transcript_turns: dict[int, str], david_wav: Path, out_dir: Path, client: TTSClient,
            pause_after: float = 0.35, max_pause: float | None = None, cache: Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = cache or out_dir / "clips"
    turns = alignment["turns"]
    clips, secs = {}, {}
    for t in turns:
        if t["speaker"] == "Eve" and transcript_turns.get(t["i"], "").strip():
            clips[t["i"]] = make_clip(client, transcript_turns[t["i"]], cache)
            secs[t["i"]] = wav_seconds(clips[t["i"]])
    ops, place = build_ops(turns, secs, pause_after, max_pause)
    tm = build_timemap(ops, wav_seconds(david_wav))
    at = {i: tm.to_edited(p, side="left") + off for i, (p, off) in place.items()}
    eve_at = [(at[i], clips[i]) for i in clips]
    d_out, e_out, m_out = out_dir / "david_edit.wav", out_dir / "eve_track.wav", out_dir / "mix.wav"
    total = render_tracks(david_wav, tm, eve_at, d_out, e_out)
    mix(d_out, e_out, m_out)
    out_turns = []
    for t in turns:
        if t["i"] in clips:
            es = at[t["i"]]
            ee = es + secs[t["i"]]
            method = "tts-placed"
        else:
            es, ee = tm.to_edited(t["raw_start"]), tm.to_edited(t["raw_end"])
            method = t["method"]
        out_turns.append({**t, "edited_start": round(es, 3), "edited_end": round(ee, 3), "edited_method": method})
    result = {"recording_method": "mic-plus-tts", "tts": client.name, "placeholder_audio": bool(getattr(client, "placeholder", True)),
              "pause_after": pause_after, "max_pause": max_pause,
              "inserted_seconds": round(sum(x for _, k, x in ops if k == "insert"), 3),
              "cut_seconds": round(sum(b - a for a, k, b in ops if k == "cut"), 3),
              "edited_duration": round(total, 3), "time_map": tm.as_json(), "turns": out_turns,
              "files": {"david_edit": str(d_out), "eve_track": str(e_out), "mix": str(m_out)}}
    (out_dir / "revoice.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


def prefetch(jobs: dict[int, tuple], cache: Path, workers: int = 4) -> tuple[dict[int, Path], dict[int, str]]:
    from concurrent.futures import ThreadPoolExecutor

    def one(item):
        i, (client, text) = item
        try:
            return i, make_clip(client, text, cache), None
        except Exception as e:  # noqa: BLE001 - reported per turn, never contains the key
            sil = SilentTTS(getattr(client, "voice", getattr(client, "voice_id", "?")))
            return i, make_clip(sil, text, cache), f"{type(e).__name__}: {str(e)[:200]}"

    clips, failed = {}, {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for n, (i, path, err) in enumerate(ex.map(one, sorted(jobs.items())), 1):
            clips[i] = path
            if err:
                failed[i] = err
            if n % 25 == 0:
                print(f"tts {n}/{len(jobs)} clips ({len(failed)} failed)", file=sys.stderr, flush=True)
    return clips, failed


def cut_clip(src: Path, start: float, end: float, dst: Path) -> Path:
    """Cut [start, end] seconds of a 48 kHz mono WAV (David's cleaned track) into dst."""
    with wave.open(str(src), "rb") as w:
        sr = w.getframerate()
        w.setpos(max(0, int(start * sr)))
        frames = w.readframes(max(0, int((end - start) * sr)))
    with wave.open(str(dst), "wb") as o:
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR)
        o.writeframes(frames)
    return dst


def synthesize_episode(turns, eve: TTSClient, narrator: TTSClient, out_dir: Path, cache: Path,
                       pause: float = 0.6, disclaimer: str | None = DISCLAIMER, lead_in: float = 0.5, workers: int = 4,
                       real_clips: dict[int, dict] | None = None, real_audio: Path | None = None,
                       ai_disclosure: str | None = None, missing_david: str = "narrator", missing_beat: float = 0.8) -> dict:
    """Fully synthetic ('tts-reconstructed') episode: disclaimer (narrator voice) + every turn in order,
    Eve's lines in Eve's voice, David's lines in the narrator voice, `pause` seconds between turns.

    Hybrid mode (real_clips given): David turns listed in real_clips ({turn: {"start", "end", "score"}} in
    seconds of `real_audio`) use his recorded voice; every other David turn is the narrator (AI-reconstructed);
    Eve is always TTS. Turns whose text is a "[transcript gap ...]" marker are never voiced.

    missing_david: 'narrator' (episodes 0-1: unrecorded David turns are read by the narrator) or 'flag' (normal
    episodes: unrecorded David turns are NOT voiced; they get source 'missing', a `missing_beat` of silence, and
    are listed in coverage.david_missing_turns so chapters, D1 and the PR can flag them)."""
    if missing_david not in ("narrator", "flag"):
        raise ValueError(f"missing_david must be 'narrator' or 'flag', not {missing_david!r}")
    real_clips = real_clips or {}
    hybrid = bool(real_clips) or real_audio is not None
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: list[tuple[float, Path]] = []
    t = lead_in
    disc = None
    if disclaimer:
        c = make_clip(narrator, disclaimer, cache)
        d = wav_seconds(c)
        parts.append((t, c)); disc = {"text": disclaimer, "start": round(t, 3), "end": round(t + d, 3), "voice": narrator.name}
        t += d + 1.0
    # prefetch every clip (bounded concurrency; the cache makes reruns free). A failed real-TTS turn is
    # filled with a PLACEHOLDER silent clip and reported in placeholder_turns.
    gaps = {tr.index for tr in turns if GAP_RE.match(tr.text.strip())}
    real = {i: v for i, v in real_clips.items() if i not in gaps}
    for i in real:
        if real_audio is None:
            raise ValueError("real_clips need real_audio")
    flagged = ({tr.index for tr in turns if tr.speaker != "Eve" and tr.index not in real and tr.index not in gaps
                and tts_text(tr.text)} if missing_david == "flag" else set())
    jobs = {tr.index: (eve if tr.speaker == "Eve" else narrator, tr.text) for tr in turns
            if tts_text(tr.text) and tr.index not in gaps and tr.index not in real and tr.index not in flagged}
    clips, failed = prefetch(jobs, cache, workers)
    rdir = out_dir / "real-clips"
    for i, v in real.items():
        rdir.mkdir(parents=True, exist_ok=True)
        clips[i] = cut_clip(real_audio, float(v["start"]), float(v["end"]), rdir / f"turn{i:04d}.wav")
    out_turns, missing = [], [tr.index for tr in turns if tr.index not in jobs and tr.index not in real and tr.index not in gaps
                              and tr.index not in flagged]
    for tr in turns:
        if tr.index in flagged:
            out_turns.append({"i": tr.index, "speaker": tr.speaker, "created_at": tr.created_at, "raw_start": None, "raw_end": None,
                              "edited_start": round(t, 3), "edited_end": round(t, 3), "method": "interpolated",
                              "edited_method": "interpolated", "score": None, "source": "missing"})
            t += missing_beat
            continue
        if tr.index in real:
            c = clips[tr.index]; d = wav_seconds(c); v = real[tr.index]
            parts.append((t, c))
            out_turns.append({"i": tr.index, "speaker": tr.speaker, "created_at": tr.created_at,
                              "raw_start": round(float(v["start"]), 3), "raw_end": round(float(v["end"]), 3),
                              "edited_start": round(t, 3), "edited_end": round(t + d, 3), "method": "asr-match",
                              "edited_method": "asr-match", "score": v.get("score"), "voice": "david-recorded", "source": "real"})
            t += d + pause
            continue
        if tr.index not in jobs:
            out_turns.append({"i": tr.index, "speaker": tr.speaker, "created_at": tr.created_at, "raw_start": None, "raw_end": None,
                              "edited_start": round(t, 3), "edited_end": round(t, 3), "method": "tts-synthetic", "edited_method": "tts-synthetic",
                              "score": None, "source": "gap" if tr.index in gaps else "empty"})
            continue
        client = jobs[tr.index][0]
        c = clips[tr.index]
        d = wav_seconds(c)
        parts.append((t, c))
        out_turns.append({"i": tr.index, "speaker": tr.speaker, "created_at": tr.created_at, "raw_start": None, "raw_end": None,
                          "edited_start": round(t, 3), "edited_end": round(t + d, 3), "method": "tts-synthetic",
                          "edited_method": "tts-synthetic", "score": None,
                          "voice": client.name if tr.index not in failed else "placeholder-silent",
                          "source": "eve-tts" if tr.speaker == "Eve" else "narrator",
                          **({"tts_error": failed[tr.index]} if tr.index in failed else {})})
        t += d + pause
    mixp = out_dir / "mix.wav"
    with wave.open(str(mixp), "wb") as o:
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(SR)
        written = 0
        for start, clip in parts:
            target = _frames(start)
            o.writeframes(b"\x00\x00" * max(0, target - written)); written = max(written, target)
            with wave.open(str(clip), "rb") as c:
                n = c.getnframes(); o.writeframes(c.readframes(n)); written += n
        o.writeframes(b"\x00\x00" * _frames(1.0)); written += _frames(1.0)
    placeholder = bool(getattr(eve, "placeholder", True) or getattr(narrator, "placeholder", True) or failed)
    david_lines = [x for x in out_turns if x["speaker"] == "David" and x["source"] in ("real", "narrator", "missing")]
    n_real = sum(x["source"] == "real" for x in david_lines)
    dur = lambda xs: round(sum(x["edited_end"] - x["edited_start"] for x in xs), 3)  # noqa: E731
    coverage = {"david_lines": len(david_lines), "david_real": n_real, "david_narrator": len(david_lines) - n_real,
                "david_real_share": round(n_real / len(david_lines), 4) if david_lines else 0.0,
                "david_real_seconds": dur([x for x in david_lines if x["source"] == "real"]),
                "david_narrator_seconds": dur([x for x in david_lines if x["source"] == "narrator"]),
                "eve_seconds": dur([x for x in out_turns if x.get("source") == "eve-tts"]),
                "gap_turns": sorted(gaps),
                **({"david_missing": len(flagged), "david_missing_turns": sorted(flagged), "missing_david": "flag"}
                   if missing_david == "flag" else {})}
    coverage["david_narrator"] = sum(x["source"] == "narrator" for x in david_lines)
    result = {"recording_method": "mic-plus-tts" if hybrid else "tts-reconstructed", "tts": eve.name,
              "ai_reconstructed": True, "ai_disclosure": (ai_disclosure or HYBRID_DISCLOSURE) if hybrid else ai_disclosure, "mode": "hybrid" if hybrid else "synthetic",
              "coverage": coverage,
              **({"alignment_method": "asr-fuzzy", "alignment_coverage": coverage["david_real_share"]} if hybrid else {}), "narrator": narrator.name, "placeholder_audio": placeholder,
              "placeholder_turns": sorted(failed) if not (getattr(eve, "placeholder", True) or getattr(narrator, "placeholder", True)) else "all",
              "tts_failures": {str(k): v for k, v in sorted(failed.items())},
              "pause": pause, "disclaimer": disc, "missing_turns": missing, "edited_duration": round(written / SR, 3),
              "time_map": [], "turns": out_turns, "files": {"mix": str(mixp)}}
    (out_dir / "revoice.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


def main(argv: list[str] | None = None, client: TTSClient | None = None, narrator: TTSClient | None = None) -> int:
    from align_audio import parse_transcript

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alignment", type=Path, help="required unless --synthetic")
    ap.add_argument("--transcript", required=True, type=Path)
    ap.add_argument("--audio", type=Path, help="David track (any format ffmpeg reads); not used with --synthetic")
    ap.add_argument("--synthetic", action="store_true", help="tts-reconstructed: voice every turn (Eve + narrator), with disclaimer")
    ap.add_argument("--real-clips", type=Path, help="hybrid (with --synthetic): JSON {turn_index: {start, end, score}} of David turns "
                                                     "whose recording matches; cut from --audio; other David turns use the narrator")
    ap.add_argument("--backend", choices=["xai", "piper", "silent"], help="default: xai if XAI_API_KEY is set, else $TTS_BACKEND or silent")
    ap.add_argument("--workers", type=int, default=4, help="concurrent TTS requests")
    ap.add_argument("--cache", type=Path, default=Path("out/tts-cache"), help="clip cache (content-hash keyed)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--language", default="en")
    ap.add_argument("--disclaimer", help="spoken opening disclaimer (narrator voice); default depends on mode")
    ap.add_argument("--disclosure", help="written AI disclosure for D1/feed/show notes (hybrid default: HYBRID_DISCLOSURE)")
    ap.add_argument("--missing-david", choices=["narrator", "flag"], default="narrator",
                    help="hybrid: unrecorded David turns read by the narrator (episodes 0-1) or flagged as missing audio and not voiced")
    ap.add_argument("--pause-after", type=float, default=0.35)
    ap.add_argument("--max-pause", type=float, default=None)
    a = ap.parse_args(argv)
    md = a.transcript.read_text(encoding="utf-8")
    turns = parse_transcript(md)
    if a.synthetic:
        eve = client or make_client("eve", a.backend)
        narr = narrator or make_client("narrator", a.backend)
        rc = {int(k): v for k, v in json.loads(a.real_clips.read_text()).items()} if a.real_clips else None
        if rc is not None and not a.audio:
            ap.error("--real-clips needs --audio (David's cleaned 48 kHz track)")
        flag = a.missing_david == "flag"
        default_disc = (FLAG_DISCLAIMER if flag else HYBRID_DISCLAIMER) if rc is not None else DISCLAIMER
        r = synthesize_episode(turns, eve, narr, a.out, a.cache, disclaimer=a.disclaimer or default_disc,
                               workers=a.workers, real_clips=rc, real_audio=a.audio if rc is not None else None,
                               ai_disclosure=a.disclosure or (FLAG_DISCLOSURE if flag and rc is not None else None),
                               missing_david=a.missing_david)
        print(json.dumps({k: r[k] for k in ("mode", "tts", "narrator", "placeholder_audio", "edited_duration", "missing_turns", "coverage")}))
        return 0
    if not (a.alignment and a.audio):
        ap.error("--alignment and --audio are required unless --synthetic")
    if client is None:
        if not os.environ.get("XAI_API_KEY") and a.backend in (None, "xai"):
            print("XAI_API_KEY not set; skipping re-voice (use --backend silent|piper for a PLACEHOLDER mix)", file=sys.stderr)
            return 0
        client = make_client("eve", a.backend)
    alignment = json.loads(a.alignment.read_text())
    text = {t.index: t.text for t in turns if t.speaker == "Eve"}
    a.out.mkdir(parents=True, exist_ok=True)
    david = a.out / "david_48k.wav"
    to_wav48(a.audio, david)
    r = revoice(alignment, text, david, a.out, client, a.pause_after, a.max_pause, cache=a.cache)
    print(json.dumps({k: r[k] for k in ("tts", "placeholder_audio", "inserted_seconds", "cut_seconds", "edited_duration")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
