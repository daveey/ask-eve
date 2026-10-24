"""Shared helpers for the audio-pipeline tests (scripts/ on sys.path, synthetic audio)."""
import array
import math
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
SHARE_JSON = Path("/workspace/share.json")
EP1 = ROOT / "episodes/2026-10-23_eve-david-podcast-planning.md"
EP1_SHA = "839cd3dfe12346f63d86df7531444eae2e29db87d307b32cb32905626052779e"  # committed (redacted) transcript
EP1_EXPORT_SHA = "15d413f1fc34283cc1bb50f31d694395979201996d803a5871ee1020b3adcea4"  # verbatim ingest_share export


def write_tone_wav(path: Path, total: float, tones: list[tuple[float, float, float]], sr: int = 48000, amp: float = 0.3):
    """Mono s16 WAV of `total` seconds; tones = [(start, end, freq)], silence elsewhere."""
    n = int(round(total * sr))
    buf = array.array("h", bytes(2 * n))
    for s, e, f in tones:
        for i in range(int(s * sr), min(n, int(e * sr))):
            buf[i] = int(amp * 32767 * math.sin(2 * math.pi * f * i / sr))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(buf.tobytes())


def rms(path: Path, start: float, end: float) -> float:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        w.setpos(min(w.getnframes(), int(start * sr)))
        a = array.array("h", w.readframes(max(0, int((end - start) * sr))))
    return math.sqrt(sum(x * x for x in a) / len(a)) / 32767 if a else 0.0


class FakeTTS:
    """Deterministic stand-in for xAI TTS: a sine clip whose length is set per text (ffmpeg lavfi),
    returned as 24 kHz WAV bytes so the 48 kHz conversion path is exercised."""

    name = "fake-tts"

    def __init__(self, durations: dict[str, float], default: float = 1.0):
        self.durations, self.default, self.calls = durations, default, []

    def synthesize(self, text: str):
        self.calls.append(text)
        d = next((v for k, v in self.durations.items() if k in text), self.default)
        out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={d}:sample_rate=24000",
                              "-c:a", "pcm_s16le", "-f", "wav", "-"], capture_output=True, check=True).stdout
        return out, "wav"
