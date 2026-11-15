"""Word timings from narration units, for the offline voice that reports none. The video itself is drawn by the
kinetic typography pipeline (pipeline/ and renderer/)."""

from dataclasses import dataclass
from typing import Any


@dataclass
class Word:
    text: str
    start: float
    end: float
    unit: int  # which spoken sentence it belongs to


def timed_words(units: list[dict[str, Any]]) -> list[Word]:
    """Every word with its time. Where the voice reported word times they are used (with the script's own spelling and
    punctuation when the counts agree); otherwise a sentence's time is shared out by word length."""
    out: list[Word] = []
    for i, u in enumerate(units):
        tokens = str(u["text"]).split()
        reported = u.get("words") or []
        if reported and len(reported) == len(tokens):
            out += [Word(tok, float(w["start"]), float(w["end"]), i) for tok, w in zip(tokens, reported, strict=True)]
        elif reported:
            out += [Word(str(w["text"]), float(w["start"]), float(w["end"]), i) for w in reported]
        elif tokens:
            total = sum(len(w) + 1 for w in tokens)
            t, span = float(u["start"]), float(u["end"]) - float(u["start"])
            for w in tokens:
                dt = span * (len(w) + 1) / total
                out.append(Word(w, t, t + dt, i))
                t += dt
    return out


def on_speech(units: list[dict[str, Any]], heard: list[tuple[float, float]]) -> list[Word]:
    """The script's words placed on the words a recogniser heard (start, end), sentence by sentence: for a voice that
    reports no word times (Chatterbox). The heard words may be in another script (a Hinglish line is read from its
    Devanagari copy) or split differently, so a sentence's words are laid over its heard words in proportion; a sentence
    the recogniser heard nothing in falls back to sharing its time by word length."""
    out: list[Word] = []
    for i, u in enumerate(units):
        tokens = str(u["text"]).split()
        start, end = float(u["start"]), float(u["end"])
        inside = [h for h in heard if start - 0.15 <= (h[0] + h[1]) / 2 <= end + 0.15]
        if not tokens or not inside:
            out += [Word(w.text, w.start, w.end, i) for w in timed_words([u])]
            continue
        n, m = len(tokens), len(inside)
        for k, tok in enumerate(tokens):
            first, last = inside[k * m // n], inside[max(k * m // n, (k + 1) * m // n - 1)]
            out.append(Word(tok, first[0], last[1], i))
    return out
