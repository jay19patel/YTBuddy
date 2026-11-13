"""Text to speech with Piper (open source, runs on the CPU, no account, no network after the first voice download)."""

import os
import tempfile
import urllib.request
import wave
from pathlib import Path

from ytstudio.domain.errors import PermanentError, TransientError

VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
SAMPLE_RATE = 22050


def voice_url(name: str) -> str:
    """ "en_US-lessac-medium" lives at en/en_US/lessac/medium/ in the voices repository."""
    code, speaker, quality = name.split("-", 2)
    return f"{VOICES_URL}/{code.split('_')[0]}/{code}/{speaker}/{quality}/{name}"


class PiperNarrator:
    def __init__(self, voices_dir: Path, voices: dict[str, str]):
        self.dir, self.voices = voices_dir, voices  # language -> voice name
        self._loaded: dict[str, object] = {}

    def _fetch(self, name: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        model = self.dir / f"{name}.onnx"
        for path in (model, self.dir / f"{name}.onnx.json"):
            if path.exists():
                continue
            part = path.with_name(f".{path.name}.part")
            try:
                urllib.request.urlretrieve(f"{voice_url(name)}{path.name.removeprefix(name)}?download=true", part)  # noqa: S310
            except OSError as e:
                raise TransientError(f"could not download the voice {name}: {e}") from e
            os.replace(part, path)
        return model

    def _voice(self, language: str) -> object:
        name = self.voices.get(language) or self.voices["en"]
        if name not in self._loaded:
            try:
                from piper import PiperVoice
            except ImportError as e:
                raise PermanentError("piper-tts is not installed (uv sync --extra media)") from e
            self._loaded[name] = PiperVoice.load(self._fetch(name))
        return self._loaded[name]

    def speak(self, lines: list[tuple[str, float]], language: str, out: Path) -> list[tuple[float, float]]:
        voice = self._voice(language)
        timings: list[tuple[float, float]] = []
        t = 0.0
        with tempfile.TemporaryDirectory() as d, wave.open(str(out), "wb") as dest:
            dest.setnchannels(1)
            dest.setsampwidth(2)
            dest.setframerate(SAMPLE_RATE)
            for i, (text, pause) in enumerate(lines):
                part = Path(d) / f"{i}.wav"
                with wave.open(str(part), "wb") as w:
                    voice.synthesize_wav(text, w)  # type: ignore[attr-defined]
                with wave.open(str(part), "rb") as r:
                    if r.getframerate() != SAMPLE_RATE or r.getnchannels() != 1:
                        raise PermanentError("this voice is not 22.05 kHz mono; pick a medium Piper voice")
                    frames = r.readframes(r.getnframes())
                    seconds = r.getnframes() / SAMPLE_RATE
                dest.writeframes(frames)
                silence = int(pause * SAMPLE_RATE)
                dest.writeframes(b"\x00\x00" * silence)
                timings.append((t, t + seconds))
                t += seconds + pause
        return timings
