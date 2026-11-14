"""Emphasis markers in a script line:

    *word*     bold                [word]    highlight box (yellow)     ~word~    strike
    {word}     accent colour       !!word!!  serif reveal (gold)

A marker may wrap several words ([best reels]) and may name a colour after a bar: [edit|white], {cheez|crimson}."""

import re
from dataclasses import dataclass

from pipeline.schema import Style, color

MARKERS: list[tuple[str, str, Style]] = [
    ("!!", "!!", "serif"), ("*", "*", "bold"), ("[", "]", "highlight"), ("~", "~", "strike"), ("{", "}", "accent"),
]  # fmt: skip
SPAN = re.compile("|".join(rf"{re.escape(a)}(?P<g{i}>.+?){re.escape(b)}" for i, (a, b, _) in enumerate(MARKERS)))
STRAY = re.compile(r"!!|[*\[\]~{}]")


@dataclass(frozen=True)
class Token:
    text: str
    style: Style = "normal"
    color: str | None = None


def parse(line: str) -> list[Token]:
    out: list[Token] = []
    pos = 0
    for m in SPAN.finditer(line):
        out += _plain(line[pos : m.start()], line)
        i = next(int(k[1:]) for k, v in m.groupdict().items() if v is not None)
        inner, _, col = m.group(f"g{i}").partition("|")
        if col:
            color(col.strip())  # raises on an unknown colour name
        out += [Token(w, MARKERS[i][2], col.strip() or None) for w in inner.split()]
        pos = m.end()
    return out + _plain(line[pos:], line)


def _plain(text: str, line: str) -> list[Token]:
    if s := STRAY.search(text):
        raise ValueError(f"unclosed emphasis marker {s.group()!r} in: {line}")
    return [Token(w) for w in text.split()]


def plain(line: str) -> str:
    """The words as spoken: markers removed."""
    return " ".join(t.text for t in parse(line))
