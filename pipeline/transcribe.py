"""words.json: every spoken word with its start and end, from faster-whisper word timestamps. When the script is
known, its own spelling is laid onto the recognised times (ASR spells Hinglish inconsistently; the script does not)."""

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

log = logging.getLogger(__name__)


class TimedWord(BaseModel):
    word: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


WORDS = TypeAdapter(list[TimedWord])


def load(path: Path) -> list[TimedWord]:
    return WORDS.validate_json(path.read_bytes())


def save(words: list[TimedWord], path: Path) -> None:
    path.write_text(json.dumps([w.model_dump() for w in words], ensure_ascii=False, indent=1), encoding="utf-8")


def norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def transcribe(audio: Path, model: str, language: str, prompt: str = "") -> list[TimedWord]:
    """Word-level ASR. Hinglish is decoded as English so it comes out in Roman script, never Devanagari."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise SystemExit("faster-whisper is not installed: uv sync --extra media (or pass --words words.json)") from e
    from ytstudio.adapters.media import decode_16k_mono

    lang = "en" if language in ("hinglish", "en") else language
    log.info("transcribing %s with faster-whisper %s (language=%s)", audio.name, model, lang)
    segments, _ = WhisperModel(model, compute_type="int8").transcribe(
        decode_16k_mono(audio),
        language=lang,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=prompt[:600] or None,
    )
    return [TimedWord(word=w.word.strip(), start=w.start, end=w.end) for s in segments for w in (s.words or [])]


def align(tokens: list[str], heard: list[TimedWord]) -> list[TimedWord]:
    """One timed word per script token, spelled as in the script. Matching words take the recognised time; a run of
    differently spelled words shares the recognised span by length; words the recogniser missed are placed in the gap
    between their neighbours."""
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    ops = SequenceMatcher(None, [norm(t) for t in tokens], [norm(w.word) for w in heard], autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (heard[j1 + k].start, heard[j1 + k].end)
        elif tag == "replace":
            times[i1:i2] = _share(tokens[i1:i2], heard[j1].start, heard[j2 - 1].end)
    for i, t in enumerate(times):  # still missing: fill each gap between known neighbours
        if t is None:
            j = i
            while j < len(times) and times[j] is None:
                j += 1
            before = times[i - 1] if i > 0 else None
            after = times[j] if j < len(times) else None
            a = before[1] if before else 0.0
            b = after[0] if after else (heard[-1].end if heard else a + 0.3 * (j - i))
            times[i:j] = _share(tokens[i:j], a, max(a, b))
    out: list[TimedWord] = []
    for tok, t in zip(tokens, times, strict=True):
        assert t is not None
        start = max(t[0], out[-1].start if out else 0.0)  # never earlier than the word before
        out.append(TimedWord(word=tok, start=round(start, 3), end=round(max(t[1], start), 3)))
    return out


def _share(tokens: list[str], a: float, b: float) -> list[tuple[float, float] | None]:
    total = sum(len(t) + 1 for t in tokens) or 1
    out: list[tuple[float, float] | None] = []
    for t in tokens:
        dt = (b - a) * (len(t) + 1) / total
        out.append((a, a + dt))
        a += dt
    return out
