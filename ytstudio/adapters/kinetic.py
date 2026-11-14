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
