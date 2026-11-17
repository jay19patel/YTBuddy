"""Text to speech. Microsoft's neural voices are the main voice (natural, Indian English and Hindi, and they report when
every word is said). Piper is the offline fallback: open source, runs on the CPU, no account, no network."""

import asyncio
import base64
import json
import logging
import math
import os
import subprocess
import tempfile
import unicodedata
import urllib.request
import wave
from array import array
from collections.abc import Callable
from difflib import Match, SequenceMatcher
from pathlib import Path
from typing import Any

from ytstudio.domain.errors import DomainError, PermanentError, TransientError
from ytstudio.domain.video import CHATTERBOX_LANGUAGE

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


CHATTERBOX_RATE = 24000
CHATTERBOX_TIMEOUT_S = 3500  # inside the media queue's 3600 s soft limit
CHATTERBOX_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "chatterbox_say.py"


Heard = list[tuple[str, float, float]]  # (word, start, end) as a recogniser heard them
CHATTERBOX_TAKES = 2  # a sentence said wrong (extra words after it, or most of it missing) is said once more
QUIET_DB = -45.0  # below this a 20 ms window is silence or a murmur
LEAD_S, TAIL_S = 0.06, 0.1  # kept before the first sound and after the last word of each sentence
# Consonants as sounds, so a line and what the recogniser heard compare in either script: it writes the English words
# of a Hinglish line in Roman letters ("ट्रेडिंग" is heard as "trading"; both are "trdng"). Vowels are left out, being
# spelled least alike.
DEVANAGARI = dict(zip("कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसहंँ", "kkggnccjjnttddnttddnpfbbmyrlvssshnn", strict=True))
DIGRAPHS = (("ch", "C"), ("sh", "s"), ("th", "t"), ("dh", "d"), ("kh", "k"), ("gh", "g"), ("bh", "b"), ("ph", "f"))
LATIN = str.maketrans({"c": "k", "q": "k", "x": "ks", "z": "j", "w": "v", "C": "c"})


def _letters(s: str) -> str:
    """The consonant sounds of `s`, Devanagari or Roman, as lower-case Roman letters (and digits)."""
    s = unicodedata.normalize("NFD", s.lower())  # क़ and ड़ are their letter plus a dot
    for two, one in DIGRAPHS:
        s = s.replace(two, one)
    s = s.translate(LATIN)
    return "".join(DEVANAGARI.get(c, c) for c in s if c in DEVANAGARI or c in "bcdfghjklmnprstvy0123456789")


def sentence_end(text: str, heard: Heard) -> tuple[int, float, float]:
    """Where `text` ends among the heard words: (index of its last heard word, share of its letters heard, letters
    heard after it as a share of its letters). The recogniser spells words its own way, so the letters are matched
    fuzzily, in order; a long run of heard letters matching nothing is where the voice went off on its own."""
    want = _letters(text)
    owner = [i for i, (w, _, _) in enumerate(heard) for _ in _letters(w)]
    if not want or not owner:
        return -1, 0.0, 0.0
    got = "".join(_letters(w) for w, _, _ in heard)
    kept: list[Match] = []
    a0 = b0 = 0
    for b in SequenceMatcher(None, want, got, autojunk=False).get_matching_blocks():
        if b.size < 2:
            continue
        if kept and (b.b - b0) - (b.a - a0) > 4:  # far more said than written before this match: babble
            break
        kept.append(b)
        a0, b0 = b.a + b.size, b.b + b.size
    if not kept:
        return -1, 0.0, len(got) / len(want)
    unsaid = min(len(want) - a0, len(want) // 3)  # letters after the last match: taken as said, misheard
    k = owner[min(len(got), b0 + unsaid) - 1]
    after = sum(len(_letters(w)) for w, _, _ in heard[k + 1 :])
    return k, sum(b.size for b in kept) / len(want), after / len(want)


def _loud(s: array, i: int, win: int) -> bool:
    chunk = s[i : i + win]
    rms = math.sqrt(sum(x * x for x in chunk) / max(1, len(chunk)))
    return 20 * math.log10(max(rms, 1) / 32768) > QUIET_DB


def trimmed(frames: bytes, rate: int, heard: Heard, k: int) -> tuple[bytes, float]:
    """16-bit mono `frames` from just before the first sound to just after heard word `k` (the sentence's last), with
    a short fade: the silence, murmur and extra words after it are dropped. Without heard words, trailing silence
    only. Returns the frames and how much was cut from the start (seconds)."""
    s, win = array("h", frames), rate // 50
    start = next((i for i in range(0, len(s), win) if _loud(s, i, win)), 0)
    start = max(0, start - int(LEAD_S * rate))
    if 0 <= k < len(heard):
        end, limit = int(heard[k][2] * rate), int(heard[k + 1][1] * rate) if k + 1 < len(heard) else len(s)
        limit = min(limit, end + int(0.4 * rate))  # the last syllable's tail, never into the next word
        while end < limit and _loud(s, end, win):
            end += win
        end = min(end + int(TAIL_S * rate), limit if k + 1 < len(heard) else len(s))
    else:
        loud = [i for i in range(0, len(s), win) if _loud(s, i, win)]
        end = min(len(s), loud[-1] + win + int(TAIL_S * rate)) if loud else len(s)
    out = s[start : max(start, end)]
    fade = min(len(out), int(0.03 * rate))
    for j in range(fade):  # no click where the cut falls
        out[len(out) - fade + j] = int(out[len(out) - fade + j] * (fade - j) / fade)
    return out.tobytes(), start / rate


class ChatterboxNarrator:
    """Chatterbox (open source). Either a server (`url`: tools/chatterbox_server.py on the Mac, using its GPU, the model
    kept loaded) or its own virtualenv's `python` running tools/chatterbox_say.py per script; the app never imports
    torch. Chatterbox reports no word times and often runs on after a sentence (silence, a murmur, made-up words):
    with `listen` (a recogniser: wav, language -> heard words) every sentence is cut where its last word ends, one
    said wrong is said again, and the heard word times go with it so the text keeps in sync."""

    def __init__(
        self,
        python: str = "",
        ref_audio: str = "",
        url: str = "",
        listen: Callable[[Path, str], Heard] | None = None,
    ):
        if url and not url.startswith(("http://", "https://")):
            raise ValueError(f"CHATTERBOX_URL must be an http(s) address, not {url!r}")
        self.python, self.ref, self.url, self.listen = python, ref_audio, url.rstrip("/"), listen

    def speak(self, lines: list[tuple[str, float]], language: str, out: Path) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            texts = [t for t, _ in lines]
            best = [
                self._judge(t, c, language, work)
                for t, c in zip(texts, self._say(texts, language, work, 0), strict=True)
            ]
            for n in range(1, CHATTERBOX_TAKES):
                redo = [i for i, take in enumerate(best) if take[0] > 0]
                if not redo:
                    break
                log.info("Chatterbox said %d of %d sentences wrong: saying them again", len(redo), len(texts))
                for i, clip in zip(redo, self._say([texts[i] for i in redo], language, work, n), strict=True):
                    take = self._judge(texts[i], clip, language, work)
                    best[i] = min(best[i], take, key=lambda x: x[0])
            timings: list[dict[str, Any]] = []
            t = 0.0
            with wave.open(str(out), "wb") as dest:
                dest.setnchannels(1)
                dest.setsampwidth(2)
                dest.setframerate(CHATTERBOX_RATE)
                for (_, pause), (_, frames, heard) in zip(lines, best, strict=True):
                    seconds = len(frames) / 2 / CHATTERBOX_RATE
                    dest.writeframes(frames)
                    dest.writeframes(b"\x00\x00" * int(pause * CHATTERBOX_RATE))
                    said = [[round(t + a, 3), round(t + b, 3)] for a, b in heard]
                    timings.append({"start": t, "end": t + seconds, "words": [], "heard": said})
                    t += seconds + pause
        return timings

    def _judge(
        self, text: str, frames: bytes, language: str, work: Path
    ) -> tuple[float, bytes, list[tuple[float, float]]]:
        """(how wrong the take is, 0 = fine; its frames cut to the sentence; heard word times within them)."""
        if not self.listen:
            return 0.0, trimmed(frames, CHATTERBOX_RATE, [], -1)[0], []
        clip = work / "take.wav"
        with wave.open(str(clip), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(CHATTERBOX_RATE)
            w.writeframes(frames)
        heard = self.listen(clip, language)
        k, said, extra = sentence_end(text, heard)
        cut, lead = trimmed(frames, CHATTERBOX_RATE, heard, k)
        seconds = len(cut) / 2 / CHATTERBOX_RATE
        times = [(max(0.0, a - lead), min(seconds, b - lead)) for _, a, b in heard[: k + 1]]
        wrong = max(0.0, extra - 0.2) + max(0.0, 0.6 - said) if heard else 0.0
        if wrong:
            log.info(
                "Chatterbox take of %r: %.0f%% heard, %.0f%% more said after it", text[:40], said * 100, extra * 100
            )
        return wrong, cut, times

    def _say(self, texts: list[str], language: str, work: Path, take: int) -> list[bytes]:
        """One 24 kHz mono 16-bit clip per text. A new `take` samples a new seed: the same one would say it the same."""
        if self.url:
            self._ask_server(texts, language, work, take)
        else:
            self._run_script(texts, language, work, take)
        clips = []
        for i in range(len(texts)):
            with wave.open(str(work / f"{i}.wav"), "rb") as r:
                if r.getframerate() != CHATTERBOX_RATE or r.getnchannels() != 1 or r.getsampwidth() != 2:
                    raise PermanentError("Chatterbox sent audio that is not 24 kHz mono")
                clips.append(r.readframes(r.getnframes()))
        return clips

    def _ask_server(self, texts: list[str], language: str, work: Path, take: int) -> None:
        req = urllib.request.Request(  # noqa: S310 - the scheme is checked to be http(s) in __init__
            f"{self.url}/speak",
            data=json.dumps({"lines": texts, "lang": language, "take": take}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=CHATTERBOX_TIMEOUT_S) as r:  # noqa: S310 - checked in __init__
                wavs = json.loads(r.read())["wavs"]
        except (OSError, ValueError, KeyError) as e:  # URLError is an OSError
            raise PermanentError(
                f"The Chatterbox server at {self.url} did not answer ({e}). Start it on the Mac: "
                "<chatterbox-venv>/bin/python tools/chatterbox_server.py"
            ) from e
        if len(wavs) != len(texts):
            raise PermanentError("The Chatterbox server sent a different number of sentences back")
        for i, b64 in enumerate(wavs):
            (work / f"{i}.wav").write_bytes(base64.b64decode(b64))

    def _run_script(self, texts: list[str], language: str, work: Path, take: int) -> None:
        (work / "lines.json").write_text(json.dumps(texts), encoding="utf-8")
        cmd = [self.python, str(CHATTERBOX_SCRIPT), "--lines", str(work / "lines.json"), "--out", str(work)]
        cmd += ["--lang", language, "--take", str(take), *(["--ref", self.ref] if self.ref else [])]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=CHATTERBOX_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise PermanentError(f"Chatterbox could not run: {e}") from e
        if p.returncode:
            raise TransientError(f"Chatterbox failed: {p.stderr[-300:]}")


class VoiceNarrator:
    """The voice picked on the form. A Chatterbox voice is only ever Chatterbox: when it cannot run the video stops
    with a message instead of quietly using a robotic voice. Other voice ids (none are offered) use the Microsoft
    voice, then the offline Piper one."""

    def __init__(self, online: EdgeNarrator, offline: PiperNarrator, chatterbox: ChatterboxNarrator | None = None):
        self.online, self.offline, self.chatterbox = online, offline, chatterbox

    def speak(self, lines: list[tuple[str, float]], voice: str, out: Path) -> tuple[list[dict[str, Any]], str]:
        if voice in CHATTERBOX_LANGUAGE:
            if not self.chatterbox:
                raise PermanentError(
                    "Chatterbox is not set up: start tools/chatterbox_server.py on the Mac and set CHATTERBOX_URL"
                )
            return self.chatterbox.speak(lines, CHATTERBOX_LANGUAGE[voice], out), "Chatterbox (open source)"
        try:
            return self.online.speak(lines, voice, out), "Microsoft neural voice"
        except DomainError as e:
            log.warning("online voice failed, using the offline voice: %s", e)
        return self.offline.speak(lines, "hi" if voice.startswith("hi-") else "en", out), "Offline voice (Piper)"
