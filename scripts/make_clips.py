#!/usr/bin/env python3
"""Generate 6 shareable 720x720 audiogram clips (.mp4 + .webp poster) for Ask Eve.

Each clip is a 30-46s highlight from a standalone Ask Eve episode rendered with a
high-legibility sans-serif editorial quote card, generous whitespace, and zero clutter.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CLIPS_JSON = ROOT / "index" / "clips.json"
CLIPS_DIR = ROOT / "site" / "static" / "assets" / "clips"
AVATAR_PATH = ROOT / "site" / "static" / "assets" / "avatars" / "thumbs" / "p01_r01.webp"

SANS_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
SANS_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def render_card_image(clip: dict, size: int = 720) -> Image.Image:
    """Render the 720x720 dark-editorial quote card with clean sans-serif type and generous whitespace."""
    bg = Image.new("RGBA", (size, size), (12, 11, 16, 255))
    draw = ImageDraw.Draw(bg)

    # Outer card frame with generous padding
    draw.rounded_rectangle(
        (32, 32, size - 32, size - 32),
        radius=22,
        fill=(18, 16, 24, 255),
        outline=(255, 255, 255, 28),
        width=1,
    )

    # Top header (x=68, y=64): Eve circular avatar (52x52) + Episode kicker + clip title
    av_size = 52
    if AVATAR_PATH.exists():
        av = Image.open(AVATAR_PATH).convert("RGBA").resize((av_size, av_size), Image.Resampling.LANCZOS)
        mask = Image.new("L", (av_size, av_size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, av_size, av_size), fill=255)
        bg.paste(av, (68, 64), mask)
        draw.ellipse((67, 63, 68 + av_size + 1, 64 + av_size + 1), outline=(255, 120, 73, 185), width=2)

    f_kicker = _font(SANS_BOLD_CANDIDATES, 14)
    f_title = _font(SANS_BOLD_CANDIDATES, 20)
    f_q_label = _font(SANS_BOLD_CANDIDATES, 13)
    f_q_text = _font(SANS_CANDIDATES, 17)
    f_quote = _font(SANS_CANDIDATES, 22)
    f_meta = _font(SANS_CANDIDATES, 15)
    f_meta_bold = _font(SANS_BOLD_CANDIDATES, 15)

    ep_num = clip.get("episode_number")
    kicker_txt = f"ASK EVE  ·  EPISODE {ep_num}" if ep_num else "ASK EVE PODCAST"
    draw.text((136, 66), kicker_txt, font=f_kicker, fill=(255, 120, 73, 255))

    title_txt = clip["title"]
    if len(title_txt) > 44:
        title_txt = title_txt[:43] + "…"
    draw.text((136, 89), title_txt, font=f_title, fill=(246, 242, 235, 255))

    y = 134
    david_q = (clip.get("david_q") or "").strip().strip("“”\"")
    if david_q:
        q_wrapped = textwrap.wrap(f"DAVID: “{david_q}”", width=56)[:3]
        box_h = 18 + len(q_wrapped) * 23
        draw.rounded_rectangle(
            (68, y, size - 68, y + box_h),
            radius=12,
            fill=(27, 24, 36, 255),
            outline=(255, 255, 255, 18),
            width=1,
        )
        qy = y + 9
        for line in q_wrapped:
            draw.text((84, qy), line, font=f_q_text, fill=(195, 188, 206, 255))
            qy += 23
        y += box_h + 20
    else:
        y = 160

    # Eve's pull-quote in crisp high-contrast sans-serif with generous line height
    draw.text((68, y), "EVE:", font=f_q_label, fill=(255, 120, 73, 255))
    y += 22
    raw_quote = (clip.get("eve_caption") or clip.get("quote") or "").strip().strip("“”\"")
    quote = f"“{raw_quote}”"
    wrapped = textwrap.wrap(quote, width=48)[:8]
    for line in wrapped:
        draw.text((68, y), line, font=f_quote, fill=(248, 245, 240, 255))
        y += 33

    # Bottom metadata (y=568, well above the y=600..720 native browser <video controls> bar)
    draw.line((68, 552, size - 68, 552), fill=(255, 255, 255, 24), width=1)
    draw.text(
        (68, 568),
        "askevepodcast.com  ·  @askevepodcast",
        font=f_meta,
        fill=(165, 157, 176, 255),
    )

    pill_txt = f"Episode {ep_num}" if ep_num else (clip.get("episode_title") or "Ask Eve")
    bbox = draw.textbbox((0, 0), pill_txt, font=f_meta_bold)
    tw = bbox[2] - bbox[0]
    draw.text(
        (size - 68 - tw, 568),
        pill_txt,
        font=f_meta_bold,
        fill=(255, 120, 73, 255),
    )

    return bg


def find_source_audio(clip: dict, default_audio: str) -> Path:
    raw = clip.get("audio_default") or default_audio
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    if p.exists():
        return p
    ep_slug = clip.get("episode_slug", "")
    for candidate in [
        ROOT / "out" / "splits" / f"2026-10-23_{ep_slug}.v1.mp3",
        ROOT / "out" / f"2026-10-23_{ep_slug}" / "produce" / f"2026-10-23_{ep_slug}.v1.mp3",
        ROOT / "out" / "en" / "2026-10-23_eve-david-podcast-planning.v3.mp3",
    ]:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No source audio found for clip {clip['id']} ({raw})")


def build_clip(clip: dict, default_audio: str) -> None:
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    slug = clip["id"]
    out_mp4 = CLIPS_DIR / f"{slug}.mp4"
    out_webp = CLIPS_DIR / f"{slug}.webp"

    card = render_card_image(clip)
    card.convert("RGB").save(out_webp, "WEBP", quality=92)

    src_audio = find_source_audio(clip, default_audio)
    start = float(clip.get("start_sec") or clip.get("start_s") or 0.0)
    dur = float(clip.get("duration_sec") or clip.get("duration_s") or 35.0)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        bg_png = td_path / "bg.png"
        card.convert("RGB").save(bg_png, "PNG")
        slice_wav = td_path / "slice.wav"

        fade_out_st = max(0.0, dur - 0.6)
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
                "-i", str(src_audio),
                "-af", f"afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_st:.2f}:d=0.6,loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar", "44100", "-ac", "1",
                str(slice_wav),
            ],
            check=True,
        )

        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-loop", "1", "-framerate", "24", "-i", str(bg_png),
                "-i", str(slice_wav),
                "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "22", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k",
                "-shortest", "-movflags", "+faststart",
                str(out_mp4),
            ],
            check=True,
        )
    print(f"Built {out_mp4.name} ({out_mp4.stat().st_size // 1024} KB) + {out_webp.name}")


def main() -> None:
    data = json.loads(CLIPS_JSON.read_text())
    default_audio = data.get("audio_default", "")
    for clip in data["clips"]:
        build_clip(clip, default_audio)


if __name__ == "__main__":
    main()
