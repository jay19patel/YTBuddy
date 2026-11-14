"""Text to speech. Microsoft's neural voices are the main voice (natural, Indian English and Hindi, and they report when
every word is said). Piper is the offline fallback: open source, runs on the CPU, no account, no network."""

import asyncio
import logging
import os
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path
from typing import Any

from ytstudio.domain.errors import DomainError, PermanentError, TransientError
from ytstudio.domain.video import voice_language

log = logging.getLogger(__name__)

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

    def speak(self, lines: list[tuple[str, float]], language: str, out: Path) -> list[dict[str, Any]]:
        voice = self._voice(language)
        timings: list[dict[str, Any]] = []
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
                timings.append({"start": t, "end": t + seconds, "words": []})
                t += seconds + pause
        return timings


EDGE_RATE = 24000  # what the service sends: 24 kHz mono MP3
EDGE_TRIES = 3
EDGE_EDGE = 0.12  # silence kept before the first and after the last word of each clip


def decode_to_wav(mp3: bytes, workdir: Path, name: str) -> Path:
    src, dest = workdir / f"{name}.mp3", workdir / f"{name}.wav"
    src.write_bytes(mp3)
    p = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-ar", str(EDGE_RATE), "-ac", "1", str(dest)],
        capture_output=True,
        check=False,
    )
    if p.returncode:
        raise TransientError(f"the voice sent audio that could not be read: {p.stderr.decode(errors='replace')[-200:]}")
    return dest


class EdgeNarrator:
    """Microsoft Edge's read-aloud neural voices through the edge-tts package. Free, no key, needs internet."""

    def __init__(self, parallel: int = 4):
        self.parallel = parallel

    async def _one(self, edge_tts: Any, text: str, voice: str, gate: asyncio.Semaphore) -> tuple[bytes, list[Any]]:
        async with gate:
            last: Exception | None = None
            for attempt in range(EDGE_TRIES):
                try:
                    audio, words = bytearray(), []
                    async for ev in edge_tts.Communicate(text, voice, boundary="WordBoundary").stream():
                        if ev["type"] == "audio":
                            audio += ev["data"]
                        elif ev["type"] == "WordBoundary":
                            words.append((ev["offset"] / 1e7, ev["duration"] / 1e7, ev["text"]))
                    if audio:
                        return bytes(audio), words
                    last = RuntimeError("no audio came back")
                except Exception as e:
                    last = e
                await asyncio.sleep(1 + attempt)
            raise TransientError(f"the Microsoft voice service did not answer: {last}")

    async def _all(self, edge_tts: Any, lines: list[tuple[str, float]], voice: str) -> list[tuple[bytes, list[Any]]]:
        gate = asyncio.Semaphore(self.parallel)
        return list(await asyncio.gather(*(self._one(edge_tts, t, voice, gate) for t, _ in lines)))

    def speak(self, lines: list[tuple[str, float]], voice: str, out: Path) -> list[dict[str, Any]]:
        try:
            import edge_tts
        except ImportError as e:
            raise PermanentError("edge-tts is not installed (uv sync --extra media)") from e
        spoken = asyncio.run(self._all(edge_tts, lines, voice))
        timings: list[dict[str, Any]] = []
        t = 0.0
        with tempfile.TemporaryDirectory() as d, wave.open(str(out), "wb") as dest:
            dest.setnchannels(1)
            dest.setsampwidth(2)
            dest.setframerate(EDGE_RATE)
            for i, ((_, pause), (mp3, words)) in enumerate(zip(lines, spoken, strict=True)):
                with wave.open(str(decode_to_wav(mp3, Path(d), str(i))), "rb") as r:
                    frames, seconds = r.readframes(r.getnframes()), r.getnframes() / EDGE_RATE
                if words:  # the service pads every clip with ~0.5 s of silence each side: keep a short breath only
                    a = max(0.0, words[0][0] - EDGE_EDGE)
                    b = min(seconds, words[-1][0] + words[-1][1] + EDGE_EDGE)
                    frames = frames[int(a * EDGE_RATE) * 2 : int(b * EDGE_RATE) * 2]
                    seconds = len(frames) / 2 / EDGE_RATE
                    words = [(off - a, dur, w) for off, dur, w in words]
                dest.writeframes(frames)
                dest.writeframes(b"\x00\x00" * int(pause * EDGE_RATE))
                timings.append(
                    {
                        "start": t,
                        "end": t + seconds,
                        "words": [{"text": w, "start": t + a, "end": t + a + dur} for a, dur, w in words],
                    }
                )
                t += seconds + pause
        return timings


class VoiceNarrator:
    """The Microsoft voice first; if the service cannot be reached, the offline Piper voice of the same language."""

    def __init__(self, online: EdgeNarrator, offline: PiperNarrator):
        self.online, self.offline = online, offline

    def speak(self, lines: list[tuple[str, float]], voice: str, out: Path) -> tuple[list[dict[str, Any]], str]:
        try:
            return self.online.speak(lines, voice, out), "Microsoft neural voice"
        except DomainError as e:
            log.warning("online voice failed, using the offline voice: %s", e)
        return self.offline.speak(lines, voice_language(voice), out), "Offline voice (Piper)"
