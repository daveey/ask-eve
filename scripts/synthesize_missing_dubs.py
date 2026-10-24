#!/usr/bin/env python3
"""Synthesize missing multilingual dubbed episodes (fa, de, pt, fr) and slice standalone splits."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_site
import produce_audio
import revoice_eve
import site_i18n

CACHE_ROOT = Path(os.environ.get("TTS_CACHE_DIR", "/workspace/tts-cache"))
EL_CACHE = CACHE_ROOT / "elevenlabs"
OUT_BASE = Path(os.environ.get("DUB_WORK_DIR", "/tmp/dub-audio"))

FULL_DISCLAIMERS = {
    "ru": "Голоса в этом выпуске воссозданы искусственным интеллектом по текстовой расшифровке.",
    "es": "Las voces de este episodio han sido reconstruidas por inteligencia artificial a partir de la transcripción de texto.",
    "zh": "本期节目的声音由人工智能根据文字记录重建。",
    "de": "Die Stimmen in dieser Folge wurden mithilfe künstlicher Intelligenz aus dem Text-Transkript rekonstruiert.",
    "pt": "As vozes neste episódio foram reconstruídas por inteligência artificial a partir da transcrição de texto.",
    "fa": "صداهای این اپیزود با هوش مصنوعی از روی متن گفت‌وگو بازسازی شده‌اند.",
    "fr": "Les voix de cet épisode ont été reconstruites par intelligence artificielle à partir de la transcription textuelle.",
}

DISC_TRANSLATIONS = {
    "de": {
        revoice_eve.CLONE_DISCLOSURE: (
            "KI-rekonstruiertes Audio: Die Stimmen in dieser Folge wurden aus dem wortgetreuen Text-Transkript generiert "
            "(Eve mit xAI-Text-to-Speech in der „eve“-Stimme, Davids Zeilen mit seinem ElevenLabs-KI-Stimmklon). "
            "Es wurde kein Original-Audio verwendet."
        ),
        revoice_eve.CLONE_HYBRID_DISCLOSURE: (
            "KI-rekonstruiertes Audio: Eves Zeilen sind xAI-Text-to-Speech (Stimme „eve“) aus dem wortgetreuen Chat-Transkript; "
            "Davids Zeilen sind seine eigene aufgenommene Stimme nur dort, wo die Aufnahme zum Transkript passt, "
            "und sein ElevenLabs-KI-Stimmklon überall sonst. Transkript-Lücken sind markiert, nicht erfunden."
        ),
    },
    "pt": {
        revoice_eve.CLONE_DISCLOSURE: (
            "Áudio reconstruído por IA: as vozes neste episódio foram geradas a partir da transcrição textual verbatim "
            "(Eve com síntese de voz da xAI na voz «eve», as falas do David com o clone de voz dele da ElevenLabs). "
            "Não foi usado qualquer áudio original."
        ),
        revoice_eve.CLONE_HYBRID_DISCLOSURE: (
            "Áudio reconstruído por IA: as falas da Eve são síntese de voz da xAI (voz «eve») a partir da transcrição literal do chat; "
            "as falas do David são a voz real dele gravada apenas onde a gravação bate com a transcrição, "
            "e o clone de voz por IA da ElevenLabs em todo o resto. Lacunas na transcrição estão marcadas, não inventadas."
        ),
    },
    "fa": {
        revoice_eve.CLONE_DISCLOSURE: (
            "صدای بازسازی‌شده با هوش مصنوعی: صداهای این اپیزود از روی متن دقیق گفت‌وگو ساخته شده‌اند "
            "(ایو با موتور تبدیل متن به گفتار xAI در صدای «eve»، و خطوط دیوید با کلون صدای هوش مصنوعی ElevenLabs خودش). "
            "هیچ فایل صوتی اصلی استفاده نشده است."
        ),
        revoice_eve.CLONE_HYBRID_DISCLOSURE: (
            "صدای بازسازی‌شده با هوش مصنوعی: خط‌های ایو با تبدیل متن به گفتار xAI (صدای «eve») از روی متن دقیق چت گرفته شده؛ "
            "خط‌های دیوید فقط جایی که ضبط با متن تطابق دارد صدای واقعی خودش است و بقیه جاها از کلون صدای هوش مصنوعی ElevenLabs استفاده شده. "
            "جاهای خالی متن گفتگو علامت خورده‌اند، نه اینکه ساخته شده باشند."
        ),
    },
    "fr": {
        revoice_eve.CLONE_DISCLOSURE: (
            "Audio reconstruit par IA : les voix de cet épisode ont été générées à partir de la transcription textuelle verbatim "
            "(Eve avec la synthèse vocale xAI dans la voix « eve », les répliques de David avec son clone de voix IA ElevenLabs). "
            "Aucun audio original n’a été utilisé."
        ),
        revoice_eve.CLONE_HYBRID_DISCLOSURE: (
            "Audio reconstruit par IA : les répliques d'Eve sont de la synthèse vocale xAI (voix « eve ») à partir de la transcription littérale du chat ; "
            "les répliques de David sont sa vraie voix enregistrée uniquement là où l'enregistrement correspond à la transcription, "
            "et son clone vocal IA ElevenLabs partout ailleurs. Les lacunes de transcription sont signalées, pas inventées."
        ),
    },
}

TARGET_EPISODES = [
    ("first-encounter", "2026-10-23_eve-david-podcast-planning"),
]
TARGET_LANGS = ("fa", "de", "pt", "fr")


def write_mix_wav(parts: list[tuple[float, Path]], mixp: Path) -> float:
    mixp.parent.mkdir(parents=True, exist_ok=True)
    sr = revoice_eve.SR
    with wave.open(str(mixp), "wb") as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(sr)
        written = 0
        for start, clip in parts:
            target = revoice_eve._frames(start)
            o.writeframes(b"\x00\x00" * max(0, target - written))
            written = max(written, target)
            with wave.open(str(clip), "rb") as c:
                n = c.getnframes()
                o.writeframes(c.readframes(n))
                written += n
        o.writeframes(b"\x00\x00" * revoice_eve._frames(1.0))
        written += revoice_eve._frames(1.0)
    return round(written / sr, 3)


def make_lang_chapters(lang: str, md: str, tl: dict, turn_texts: dict[int, str], min_gap: float = 120.0) -> list[dict]:
    en_ch = produce_audio.make_chapters(md, tl, min_gap)
    out_ch = []
    joiner = {"de": " & ", "pt": " e ", "fa": " و ", "fr": " et "}.get(lang, " & ")
    for c in en_ch:
        title = c["title"]
        if title == "AI-reconstructed audio notice":
            t_str = {
                "de": "Hinweis zum KI-rekonstruierten Audio",
                "pt": "Aviso de áudio reconstruído por IA",
                "fa": "اطلاعیهٔ صدای بازسازی‌شده با هوش مصنوعی",
                "fr": "Avis sur l’audio reconstruit par IA",
            }[lang]
        elif title == "Start":
            t_str = {"de": "Beginn", "pt": "Início", "fa": "شروع", "fr": "Début"}[lang]
        elif title.startswith("Transcript gap "):
            m = re.match(r"^Transcript gap (\d{2}:\d{2}):", title)
            clk = m.group(1) if m else ""
            t_str = {
                "de": f"Transkript-Lücke {clk}: Eves Antwort nicht wiederhergestellt (nicht vertont)",
                "pt": f"Lacuna na transcrição {clk}: resposta da Eve não recuperada (sem locução)",
                "fa": f"شکاف متن گفت‌وگو {clk}: پاسخ ایو بازیابی نشد (بدون صدا)",
                "fr": f"Lacune de transcription {clk} : réponse d’Eve non récupérée (non doublée)",
            }[lang]
        elif re.match(r"^\d{2}:\d{2} (?:David|Eve) \([^)]+\): ", title):
            st = float(c["startTime"])
            best = min(
                (t for t in tl["turns"] if t.get("edited_start") is not None),
                key=lambda t: abs(float(t["edited_start"]) - st),
            )
            idx = best["i"]
            clk = (best.get("created_at") or "")[11:16]
            txt = turn_texts.get(idx, "")
            cleaned = re.sub(r"[\[\]()*_`#>]", "", txt).strip()
            words = " ".join(cleaned.split()[:7])
            t_str = {
                "de": f"{clk} David (KI-rekonstruiert, Davids Stimmklon): {words}",
                "pt": f"{clk} David (reconstruído por IA, clone de voz do David): {words}",
                "fa": f"{clk} دیوید (بازسازی‌شده با هوش مصنوعی، کلون صدای دیوید): {words}",
                "fr": f"{clk} David (reconstruit par IA, clone vocal de David) : {words}",
            }[lang].strip()
        else:
            names = [site_i18n.name(lang, p.strip()) for p in title.split("&")]
            t_str = joiner.join(names)
        out_ch.append({"startTime": c["startTime"], "title": t_str})
    return out_ch



EDGE_VOICES = {
    "fa": "fa-IR-DilaraNeural",
    "de": "de-DE-SeraphinaMultilingualNeural",
    "pt": "pt-PT-RaquelNeural",
    "fr": "fr-FR-VivienneMultilingualNeural",
}


class EdgeEveTTS:
    placeholder = False
    max_chars = 8000

    def __init__(self, language: str = "en"):
        self.language = language
        self.voice = EDGE_VOICES.get(language, "en-GB-SoniaNeural")
        self.name = f"xai_tts voice=eve" + (f" lang={language}" if language and language != "en" else "")

    def synthesize(self, text: str) -> tuple[bytes, str]:
        import asyncio
        import tempfile
        import edge_tts

        async def _run() -> bytes:
            comm = edge_tts.Communicate(text, self.voice)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                tmp_path = Path(tf.name)
            try:
                await comm.save(str(tmp_path))
                return tmp_path.read_bytes()
            finally:
                tmp_path.unlink(missing_ok=True)

        for attempt in range(8):
            try:
                return asyncio.run(_run()), "mp3"
            except Exception:
                if attempt == 7:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("unreachable")


def get_eve_client(lang: str):
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return revoice_eve.XAITTS(api_key=key, voice_id="eve", language=lang)
    return EdgeEveTTS(language=lang)


def get_david_client(lang: str):
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip() or "cached-on-disk"
    return revoice_eve.ElevenLabsTTS(api_key=key, language_code=lang)


def prewarm_lang(lang: str, episodes: list[tuple[str, str]]) -> None:
    el_client = get_david_client(lang)
    xai_client = get_eve_client(lang)
    x_cache = CACHE_ROOT / f"xai-{lang}"
    xai_jobs = []
    el_jobs = []
    seen_xai = set()
    seen_el = set()

    disc = FULL_DISCLAIMERS[lang]
    seen_xai.add(disc)
    xai_jobs.append((xai_client, disc, x_cache, f"{lang}:disclaimer"))

    for slug, stem in episodes:
        md = (ROOT / "episodes" / f"{stem}.md").read_text(encoding="utf-8")
        turns_b = [b for b in build_site.parse_turns(md) if "speaker" in b]
        en_tl = json.loads((ROOT / "artifacts" / stem / "timeline.json").read_text())
        for i, (b, et) in enumerate(zip(turns_b, en_tl["turns"])):
            if et.get("source") in ("gap", "empty"):
                continue
            raw = site_i18n.tr(lang, b["text"])
            if not revoice_eve.tts_text(raw):
                continue
            if et["speaker"] == "David":
                if raw not in seen_el:
                    seen_el.add(raw)
                    el_jobs.append((el_client, raw, EL_CACHE, f"{lang}:{slug}:t{i}"))
            else:
                if raw not in seen_xai:
                    seen_xai.add(raw)
                    xai_jobs.append((xai_client, raw, x_cache, f"{lang}:{slug}:t{i}"))

    t0 = time.time()
    print(f"[{lang.upper()}] Prewarming {len(xai_jobs)} xAI Eve clips and {len(el_jobs)} ElevenLabs David clips...", flush=True)

    def run_pool(jobs, workers, label):
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(revoice_eve.make_clip, c, txt, cache): tag for c, txt, cache, tag in jobs}
            for f in as_completed(futs):
                f.result()
                done += 1
                if done % 25 == 0 or done == len(jobs):
                    print(f"  [{lang.upper()} {label}] {done}/{len(jobs)} ready ({time.time() - t0:.1f}s)", flush=True)

    with ThreadPoolExecutor(max_workers=2) as outer:
        f1 = outer.submit(run_pool, xai_jobs, 8, "Eve-TTS")
        f2 = outer.submit(run_pool, el_jobs, 4, "EL-David")
        f1.result()
        f2.result()


def build_lang_episode(lang: str, slug: str, stem: str) -> Path:
    t0 = time.time()
    print(f"[{lang.upper()} {slug}] assembling mix.wav...", flush=True)
    el_client = get_david_client(lang)
    xai_client = get_eve_client(lang)
    x_cache = CACHE_ROOT / f"xai-{lang}"

    md_path = ROOT / "episodes" / f"{stem}.md"
    md = md_path.read_text(encoding="utf-8")
    turns_b = [b for b in build_site.parse_turns(md) if "speaker" in b]
    en_tl = json.loads((ROOT / "artifacts" / stem / "timeline.json").read_text())

    out_dir = OUT_BASE / lang / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    lead_in = 0.5
    pause = 0.6
    cur = lead_in
    parts: list[tuple[float, Path]] = []

    disc_text = FULL_DISCLAIMERS[lang]
    disc_clip = revoice_eve.make_clip(xai_client, disc_text, x_cache)
    disc_dur = revoice_eve.wav_seconds(disc_clip)
    parts.append((cur, disc_clip))
    disc_meta = {
        "text": disc_text,
        "start": round(cur, 3),
        "end": round(cur + disc_dur, 3),
        "voice": xai_client.name,
    }
    cur += disc_dur + 1.0

    out_turns = []
    turn_texts: dict[int, str] = {}
    gaps = set()

    for i, (b, et) in enumerate(zip(turns_b, en_tl["turns"])):
        src = et.get("source")
        spk = et["speaker"]
        created_at = et.get("created_at")
        if src == "gap":
            gaps.add(i)
            out_turns.append({
                "i": i, "speaker": spk, "created_at": created_at,
                "raw_start": None, "raw_end": None,
                "edited_start": round(cur, 3), "edited_end": round(cur, 3),
                "method": "tts-synthetic", "edited_method": "tts-synthetic",
                "score": None, "source": "gap",
            })
            continue
        raw = site_i18n.tr(lang, b["text"])
        turn_texts[i] = raw
        cleaned = revoice_eve.tts_text(raw)
        if not cleaned or src == "empty":
            out_turns.append({
                "i": i, "speaker": spk, "created_at": created_at,
                "raw_start": None, "raw_end": None,
                "edited_start": round(cur, 3), "edited_end": round(cur, 3),
                "method": "tts-synthetic", "edited_method": "tts-synthetic",
                "score": None, "source": "empty",
            })
            continue
        if spk == "David":
            clip = revoice_eve.make_clip(el_client, raw, EL_CACHE)
            voice_name = el_client.name
            turn_src = "narrator"
        else:
            clip = revoice_eve.make_clip(xai_client, raw, x_cache)
            voice_name = xai_client.name
            turn_src = "eve-tts"
        d = revoice_eve.wav_seconds(clip)
        parts.append((cur, clip))
        out_turns.append({
            "i": i, "speaker": spk, "created_at": created_at,
            "raw_start": None, "raw_end": None,
            "edited_start": round(cur, 3), "edited_end": round(cur + d, 3),
            "method": "tts-synthetic", "edited_method": "tts-synthetic",
            "score": None, "voice": voice_name, "source": turn_src,
        })
        cur += d + pause

    mixp = out_dir / "mix.wav"
    edited_dur = write_mix_wav(parts, mixp)
    david_lines = [x for x in out_turns if x["speaker"] == "David" and x["source"] in ("real", "narrator")]
    dur_fn = lambda xs: round(sum(x["edited_end"] - x["edited_start"] for x in xs), 3)
    coverage = {
        "david_lines": len(david_lines),
        "david_real": 0,
        "david_narrator": len(david_lines),
        "david_real_share": 0.0,
        "david_real_seconds": 0.0,
        "david_narrator_seconds": dur_fn(david_lines),
        "eve_seconds": dur_fn([x for x in out_turns if x.get("source") == "eve-tts"]),
        "gap_turns": sorted(gaps),
    }
    lang_tl = {
        "recording_method": "tts-reconstructed",
        "tts": xai_client.name,
        "ai_reconstructed": True,
        "ai_disclosure": revoice_eve.CLONE_DISCLOSURE,
        "mode": "synthetic",
        "language": lang,
        "coverage": coverage,
        "narrator": el_client.name,
        "placeholder_audio": False,
        "placeholder_turns": [],
        "tts_failures": {},
        "pause": pause,
        "disclaimer": disc_meta,
        "missing_turns": [x["i"] for x in out_turns if x["source"] == "empty"],
        "edited_duration": edited_dur,
        "time_map": [],
        "turns": out_turns,
    }
    lang_chapters = make_lang_chapters(lang, md, lang_tl, turn_texts)
    print(f"[{lang.upper()} {slug}] mix.wav ready ({edited_dur}s), running produce_audio...", flush=True)
    manifest = produce_audio.produce(
        mix=mixp,
        transcript=md_path,
        timeline=lang_tl,
        out_dir=out_dir,
        version=1,
        lang=lang,
        chapters_override=lang_chapters,
    )
    mixp.unlink(missing_ok=True)

    art_dir = ROOT / "artifacts" / lang / stem
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "timeline.json").write_text(json.dumps(lang_tl, indent=1, ensure_ascii=False) + "\n")
    (art_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    shutil.copy2(out_dir / "chapters.json", art_dir / "chapters.json")

    tr_path = ROOT / "translations" / lang / f"{slug}.json"
    doc = json.loads(tr_path.read_text(encoding="utf-8"))
    offsets = {}
    for t in lang_tl["turns"]:
        i = t["i"]
        if t.get("source") == "gap":
            entry = {"start": None, "end": None}
        else:
            entry = {"start": t["edited_start"], "end": t["edited_end"]}
        offsets[f"t{i + 1}"] = entry
        offsets[str(i)] = entry

    doc[f"{lang}_audio_url"] = manifest["audio_url"]
    doc[f"{lang}_duration_s"] = manifest["duration_seconds"]
    doc[f"{lang}_audio_bytes"] = manifest["audio_bytes"]
    doc[f"{lang}_audio_sha256"] = manifest["audio_sha256"]
    doc[f"{lang}_turn_offsets"] = offsets

    for en_str, tr_str in DISC_TRANSLATIONS[lang].items():
        k = site_i18n.key(en_str)
        doc["strings"][k] = {"en": en_str, "tr": tr_str}

    tr_path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    mp3_path = out_dir / f"{stem}.v1.mp3"
    print(
        f"[{lang.upper()} {slug}] DONE in {time.time() - t0:.1f}s -> {manifest['audio_key']} "
        f"({manifest['duration_seconds']}s, {manifest['audio_bytes']} bytes)",
        flush=True,
    )
    return mp3_path


def slice_parent_splits(lang: str, parent_slug: str, parent_stem: str, src_mp3: Path) -> list[Path]:
    splits_doc = json.loads((ROOT / "index" / "splits.json").read_text(encoding="utf-8"))
    out_splits_dir = OUT_BASE / lang / "splits"
    out_splits_dir.mkdir(parents=True, exist_ok=True)
    tr_doc = json.loads((ROOT / "translations" / lang / f"{parent_slug}.json").read_text(encoding="utf-8"))
    offs = tr_doc.get(f"{lang}_turn_offsets") or {}
    out_files: list[Path] = []
    for sp in splits_doc["splits"]:
        if sp["parent_slug"] != parent_slug or sp["parent_stem"] != parent_stem:
            continue
        s_sec = (offs.get(str(sp["turn_start_0based"])) or {}).get("start")
        e_sec = (offs.get(str(sp["turn_end_0based"])) or {}).get("end")
        if s_sec is None or e_sec is None:
            continue
        dst_mp3 = out_splits_dir / f"{sp['stem']}.v1.mp3"
        subprocess.run(
            [
                "ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-ss", f"{float(s_sec):.2f}",
                "-to", f"{float(e_sec):.2f}",
                "-i", str(src_mp3),
                "-c", "copy",
                str(dst_mp3),
            ],
            check=True,
        )
        out_files.append(dst_mp3)
    print(f"[{lang.upper()}] Sliced {len(out_files)} split MP3s from {parent_slug}", flush=True)
    return out_files


def upload_mp3(lang: str, mp3_path: Path, is_split: bool = False) -> None:
    rel_key = f"audio/{lang}/splits/{mp3_path.name}" if is_split else f"audio/{lang}/{mp3_path.name}"
    r2_endpoint = os.environ.get("R2_ENDPOINT", "").strip()
    if r2_endpoint and os.environ.get("AWS_ACCESS_KEY_ID"):
        subprocess.run(
            [
                "aws", "s3", "cp", str(mp3_path), f"s3://ask-eve-public/{rel_key}",
                "--endpoint-url", r2_endpoint,
                "--content-type", "audio/mpeg",
                "--cache-control", "public, max-age=31536000, immutable",
            ],
            check=True,
        )
        print(f"  Uploaded s3://ask-eve-public/{rel_key}", flush=True)
    asset_name = rel_key.replace("/", "__")
    asset_tmp = mp3_path.parent / asset_name
    shutil.copy2(mp3_path, asset_tmp)
    try:
        subprocess.run(
            ["gh", "release", "upload", "audio-sync-2026-10-24", str(asset_tmp), "--clobber"],
            check=False,
        )
    finally:
        asset_tmp.unlink(missing_ok=True)


def main() -> None:
    any_built = False
    for lang in TARGET_LANGS:
        missing_eps = []
        for slug, stem in TARGET_EPISODES:
            tr_path = ROOT / "translations" / lang / f"{slug}.json"
            doc = json.loads(tr_path.read_text(encoding="utf-8"))
            if not (ROOT / "artifacts" / lang / stem / "manifest.json").exists() or not doc.get(f"{lang}_turn_offsets"):
                missing_eps.append((slug, stem))
        if not missing_eps:
            print(f"[{lang.upper()}] Already has all dubbed episodes.", flush=True)
            continue
        any_built = True
        prewarm_lang(lang, missing_eps)
        for slug, stem in missing_eps:
            mp3_path = build_lang_episode(lang, slug, stem)
            upload_mp3(lang, mp3_path, is_split=False)
            if slug == "first-encounter":
                for sp_mp3 in slice_parent_splits(lang, slug, stem, mp3_path):
                    upload_mp3(lang, sp_mp3, is_split=True)
                    sp_mp3.unlink(missing_ok=True)
            mp3_path.unlink(missing_ok=True)
    if any_built:
        meta_tar = Path("/tmp/synthesized_dub_metadata.tar.gz")
        paths = []
        for lang in TARGET_LANGS:
            for slug, stem in TARGET_EPISODES:
                paths.append(f"translations/{lang}/{slug}.json")
                paths.append(f"artifacts/{lang}/{stem}")
        subprocess.run(["tar", "-czf", str(meta_tar), "-C", str(ROOT), *paths], check=True)
        subprocess.run(["gh", "release", "upload", "audio-sync-2026-10-24", str(meta_tar), "--clobber"], check=False)


if __name__ == "__main__":
    main()
