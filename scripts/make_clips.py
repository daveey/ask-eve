#!/usr/bin/env python3
"""scripts/make_clips.py — Render subtitled 720x720 audiogram clips (.mp4 + .webp poster) for Ask Eve.

Reads clip definitions from `index/clips.json`, slices the episode audio (or synthesizes a clean
test waveform envelope when the master MP3 is absent in CI/unit tests), renders a high-contrast
nocturnal Ask Eve card via Pillow, and overlays a real-time animated waveform via `ffmpeg`.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import subprocess
import tempfile
import textwrap
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = ROOT / "index/clips.json"
DEFAULT_OUT_DIR = ROOT / "site/static/assets/clips"
AVATAR_PATH = ROOT / "site/static/assets/avatars/full/eve-winner-512.webp"

SERIF_CANDIDATES = [
    "/usr/share/fonts/truetype/sand-box/google/Fraunces/Fraunces-VariableFont_SOFT,WONK,opsz,wght.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
]
SANS_CANDIDATES = [
    "/usr/share/fonts/truetype/sand-box/google/Inter/Inter-VariableFont_opsz,wght.ttf",
    "/usr/share/fonts/truetype/sand-box/google/Geist/Geist-VariableFont_wght.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
MONO_CANDIDATES = [
    "/usr/share/fonts/truetype/sand-box/google/JetBrains Mono/JetBrainsMono-VariableFont_wght.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


def _load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int, max_lines: int = 6) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        trial = " ".join(cur + [w])
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
            if len(lines) >= max_lines - 1:
                break
    if cur and len(lines) < max_lines:
        used = sum(len(x.split()) for x in lines)
        rem = words[used:]
        tail = " ".join(rem)
        while draw.textbbox((0, 0), tail, font=font)[2] > max_width and len(rem) > 1:
            rem = rem[:-1]
            tail = " ".join(rem).rstrip(".,;:") + "…"
        lines.append(tail)
    return lines[:max_lines]


def render_card_image(clip: dict[str, Any], size: int = 720) -> Image.Image:
    """Render the 720x720 nocturnal Ask Eve audiogram frame."""
    img = Image.new("RGBA", (size, size), (11, 13, 18, 255))
    draw = ImageDraw.Draw(img)

    # Subtle ambient card frame
    draw.rounded_rectangle((20, 20, size - 20, size - 20), radius=24, fill=(19, 17, 26, 255), outline=(48, 41, 56, 255), width=2)
    # Top copper accent bar
    draw.rounded_rectangle((44, 20, size - 44, 24), radius=2, fill=(224, 159, 90, 255))

    f_eyebrow = _load_font(MONO_CANDIDATES, 14)
    f_title = _load_font(SERIF_CANDIDATES, 22)
    f_label = _load_font(MONO_CANDIDATES, 13)
    f_q = _load_font(SANS_CANDIDATES, 17)
    f_quote = _load_font(SERIF_CANDIDATES, 21)
    f_foot = _load_font(MONO_CANDIDATES, 14)

    # Avatar in top-left (72x72 circular crop with copper ring)
    av_x, av_y, av_sz = 44, 44, 72
    if AVATAR_PATH.exists():
        av = Image.open(AVATAR_PATH).convert("RGBA").resize((av_sz, av_sz), Image.Resampling.LANCZOS)
        mask = Image.new("L", (av_sz, av_sz), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, av_sz - 1, av_sz - 1), fill=255)
        img.paste(av, (av_x, av_y), mask)
    draw.ellipse((av_x - 2, av_y - 2, av_x + av_sz + 2, av_y + av_sz + 2), outline=(224, 159, 90, 255), width=2)

    # Header metadata beside avatar
    ep_lbl = f"ASK EVE · {clip.get('episode_title', 'First Encounter').upper()} · {clip.get('clock_time', '')} UTC"
    draw.text((132, 48), ep_lbl, font=f_eyebrow, fill=(224, 159, 90, 255))
    title_lines = _wrap_text(draw, clip["title"], f_title, size - 180, max_lines=2)
    ty = 70
    for line in title_lines:
        draw.text((132, ty), line, font=f_title, fill=(244, 239, 230, 255))
        ty += 26

    # David Question Panel
    q_top = 136
    draw.rounded_rectangle((44, q_top, size - 44, q_top + 104), radius=14, fill=(26, 24, 34, 255), outline=(46, 42, 58, 255), width=1)
    draw.text((62, q_top + 14), "DAVID · QUESTION", font=f_label, fill=(168, 159, 145, 255))
    q_lines = _wrap_text(draw, clip["david_q"], f_q, size - 128, max_lines=3)
    qy = q_top + 36
    for line in q_lines:
        draw.text((62, qy), line, font=f_q, fill=(218, 211, 200, 255))
        qy += 22

    # Eve Quote Panel
    e_top = 256
    draw.rounded_rectangle((44, e_top, size - 44, 504), radius=16, fill=(23, 20, 31, 255), outline=(74, 54, 44, 255), width=1)
    draw.rounded_rectangle((44, e_top + 16, 48, 488), radius=2, fill=(224, 159, 90, 255))
    draw.text((66, e_top + 16), f"EVE · VERBATIM TAKE ON {clip.get('person_name', '').upper()}", font=f_label, fill=(127, 179, 166, 255))
    quote_lines = _wrap_text(draw, clip["eve_caption"], f_quote, size - 136, max_lines=7)
    ey = e_top + 44
    for line in quote_lines:
        draw.text((66, ey), line, font=f_quote, fill=(244, 239, 230, 255))
        ey += 28

    # Waveform box (y=520..626)
    w_top, w_bot = 520, 626
    draw.rounded_rectangle((44, w_top, size - 44, w_bot), radius=14, fill=(14, 13, 20, 255), outline=(42, 38, 52, 255), width=1)
    draw.text((62, w_top + 10), "VOICE WAVEFORM · EVE (xAI) + DAVID (ELEVENLABS CLONE)", font=f_label, fill=(120, 114, 106, 255))

    # Footer receipt bar
    receipt_short = clip.get("receipt_url", "https://askevepodcast.com").replace("https://", "")
    draw.text((44, 650), f"Receipt & full transcript: {receipt_short}", font=f_foot, fill=(224, 159, 90, 255))
    handle_txt = "@askevepodcast"
    hb = draw.textbbox((0, 0), handle_txt, font=f_foot)
    draw.text((size - 44 - (hb[2] - hb[0]), 650), handle_txt, font=f_foot, fill=(127, 179, 166, 255))

    return img


def _synthesize_fallback_wav(wav_path: Path, duration_sec: float, sample_rate: int = 24000) -> None:
    """Generate a warm speech-cadence modulated audio envelope when the master MP3 is not on disk."""
    n_samples = max(int(duration_sec * sample_rate), sample_rate)
    buf = bytearray()
    for i in range(n_samples):
        t = i / sample_rate
        syllable_env = 0.45 + 0.45 * math.sin(2 * math.pi * 3.2 * t) * math.cos(2 * math.pi * 0.7 * t)
        carrier = (
            0.55 * math.sin(2 * math.pi * 195.0 * t)
            + 0.30 * math.sin(2 * math.pi * 390.0 * t)
            + 0.15 * math.sin(2 * math.pi * 585.0 * t)
        )
        val = int(max(-1.0, min(1.0, syllable_env * carrier * 0.35)) * 32767)
        buf += struct.pack("<h", val)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(buf))


def build_clip(
    clip: dict[str, Any],
    *,
    out_dir: Path,
    audio_path: Path | None = None,
    max_duration: float | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    clip_id = clip["id"]
    mp4_path = out_dir / f"{clip_id}.mp4"
    webp_path = out_dir / f"{clip_id}.webp"

    start_sec = float(clip["start_sec"])
    end_sec = float(clip["end_sec"])
    dur = max(1.0, end_sec - start_sec)
    if max_duration is not None:
        dur = min(dur, float(max_duration))

    card_img = render_card_image(clip, size=720)

    # Save poster frame with decorative static waveform bars so the poster looks great before play
    poster_img = card_img.copy()
    pdraw = ImageDraw.Draw(poster_img)
    bars = 56
    bx0, bw_total = 62, 720 - 124
    step = bw_total / bars
    for b in range(bars):
        phase = (b / bars) * math.pi * 4.0 + (hash(clip_id) % 17)
        amp = int(8 + 24 * abs(math.sin(phase) * math.cos(phase * 0.45)))
        cx = int(bx0 + b * step)
        cy = 582
        color = (224, 159, 90, 230) if b % 3 != 0 else (127, 179, 166, 220)
        pdraw.rounded_rectangle((cx, cy - amp, cx + 6, cy + amp), radius=3, fill=color)
    poster_img.convert("RGB").save(webp_path, format="WEBP", quality=86)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        bg_png = tmp_dir / "bg.png"
        card_img.convert("RGB").save(bg_png, format="PNG")
        slice_wav = tmp_dir / "slice.wav"

        if audio_path and audio_path.exists():
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{start_sec:.2f}",
                    "-t",
                    f"{dur:.2f}",
                    "-i",
                    str(audio_path),
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    str(slice_wav),
                ],
                check=True,
            )
        else:
            _synthesize_fallback_wav(slice_wav, dur)

        filter_complex = (
            "[0:a]showwaves=s=600x76:mode=cline:rate=20:colors=#e09f5a|#7fb3a6:scale=sqrt[sw];"
            "[1:v][sw]overlay=60:542:format=auto[v]"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(slice_wav),
                "-loop",
                "1",
                "-framerate",
                "20",
                "-i",
                str(bg_png),
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "0:a",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-tune",
                "stillimage",
                "-crf",
                "26",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(mp4_path),
            ],
            check=True,
        )

    return {
        "id": clip_id,
        "mp4": str(mp4_path),
        "mp4_bytes": mp4_path.stat().st_size,
        "webp": str(webp_path),
        "webp_bytes": webp_path.stat().st_size,
        "duration_sec": round(dur, 1),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", type=Path, default=DEFAULT_SPEC, help="Path to index/clips.json")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory for .mp4 and .webp")
    ap.add_argument("--audio", type=Path, default=None, help="Override master audio path")
    ap.add_argument("--clip", type=str, default=None, help="Only render a single clip id")
    ap.add_argument("--max-duration", type=float, default=None, help="Optional cap on duration (for fast tests)")
    args = ap.parse_args(argv)

    spec = json.loads(args.spec.read_text())
    audio_path = args.audio or (Path(spec["audio_default"]) if spec.get("audio_default") else None)
    clips = spec.get("clips", [])
    if args.clip:
        clips = [c for c in clips if c["id"] == args.clip]
        if not clips:
            raise SystemExit(f"Clip id not found: {args.clip}")

    results = []
    for c in clips:
        c_audio = args.audio or (Path(c["audio_default"]) if c.get("audio_default") else audio_path)
        res = build_clip(c, out_dir=args.out_dir, audio_path=c_audio, max_duration=args.max_duration)
        results.append(res)
    print(json.dumps({"rendered": len(results), "clips": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
