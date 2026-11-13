"""Kinetic typography as an ASS subtitle file: every word pops in when it is said, the spoken word is highlighted, a bar
draws under each card and a progress bar crosses the screen. ffmpeg's libass draws it, so it is fast and sharp."""

import re
from dataclasses import dataclass
from typing import Any

from PIL import ImageFont

from ytstudio.adapters.media import font
from ytstudio.domain.video import Look

DEVANAGARI = re.compile("[ऀ-ॿ]")
STOPS = ".?!,;:।"
HOLD = 0.18  # a card stays this long after its last word
GAP = 0.45  # a pause this long starts a new card


@dataclass
class Word:
    text: str
    start: float
    end: float
    unit: int  # which spoken sentence it belongs to


@dataclass
class Placed:
    word: Word
    x: float
    y: float
    size: int
    face: str


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


def cards(words: list[Word], max_words: int, max_chars: int) -> list[list[Word]]:
    """Groups of words shown together. A sentence (or a clause, or what is said between two pauses) is split into evenly
    sized cards, so there is never a lone word left over."""
    segments: list[list[Word]] = []
    for w in words:
        if segments and not (
            w.unit != segments[-1][-1].unit
            or w.start - segments[-1][-1].end > GAP
            or segments[-1][-1].text[-1] in STOPS
        ):
            segments[-1].append(w)
        else:
            segments.append([w])
    out: list[list[Word]] = []
    for seg in segments:
        chars = sum(len(w.text) + 1 for w in seg)
        n = max(-(-len(seg) // max_words), -(-chars // max_chars), 1)
        each = -(-len(seg) // n)
        out += [seg[i : i + each] for i in range(0, len(seg), each)]
    return out


def face_of(text: str) -> str:
    return "Noto Sans Devanagari" if DEVANAGARI.search(text) else "Noto Sans"


def pil_font(text: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return font(size, bool(DEVANAGARI.search(text)))


def ass_size(text: str, size: int) -> int:
    """libass sizes a font by its full line height (ascent + descent), Pillow by its em: convert so widths agree."""
    f = pil_font(text, size)
    if hasattr(f, "getmetrics"):
        ascent, descent = f.getmetrics()
        return ascent + descent
    return size


def layout(card: list[Word], width: int, height: int, size: int, upper: bool) -> tuple[list[Placed], float, float]:
    """Word positions (centres) for a card, centred on the screen, wrapped to 86 % of the width.
    Returns them with the block's centre y and half height."""
    texts = [w.text.upper() if upper else w.text for w in card]
    limit = width * 0.86
    widest = max(pil_font(t, size).getlength(t) for t in texts)
    if widest > limit:  # one very long word: shrink the card instead of running off the screen
        size = int(size * limit / widest)
    space = pil_font("a", size).getlength(" ") or size * 0.3
    rows: list[list[int]] = [[]]
    row_w = 0.0
    for i, t in enumerate(texts):
        w = pil_font(t, size).getlength(t)
        if rows[-1] and row_w + space + w > limit:
            rows.append([])
            row_w = 0.0
        row_w += (space if rows[-1] else 0) + w
        rows[-1].append(i)
    step = size * 1.3
    top = height * 0.47 - (len(rows) * step) / 2
    placed: list[Placed] = []
    for r, idxs in enumerate(rows):
        widths = [pil_font(texts[i], size).getlength(texts[i]) for i in idxs]
        x = (width - (sum(widths) + space * (len(idxs) - 1))) / 2
        for i, w in zip(idxs, widths, strict=True):
            placed.append(Placed(card[i], x + w / 2, top + r * step + step / 2, size, face_of(texts[i])))
            x += w + space
    return sorted(placed, key=lambda p: card.index(p.word)), height * 0.47, len(rows) * step / 2


def stamp(t: float) -> str:
    t = max(t, 0.0)
    cs = round(t * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def bgr(hex_rgb: str) -> str:
    return f"&H{hex_rgb[4:6]}{hex_rgb[2:4]}{hex_rgb[0:2]}&"


def clean(text: str) -> str:
    return re.sub(r"[{}\\]", "", text)


def build_ass(units: list[dict[str, Any]], total: float, vertical: bool, look: Look) -> str:
    width, height = (1080, 1920) if vertical else (1920, 1080)
    size = 112 if vertical else 92
    groups = cards(timed_words(units), 3 if vertical else 6, 20 if vertical else 40)
    ink, accent = bgr(look.ink), bgr(look.accent)
    lines: list[str] = []
    bar_h = 14 if vertical else 12
    lines.append(
        f"Dialogue: 1,{stamp(0)},{stamp(total)},Word,,0,0,0,,"
        f"{{\\an7\\pos(0,{height - bar_h})\\p1\\1c{accent}\\bord0\\shad0\\fscx0\\t(0,{int(total * 1000)},\\fscx100)}}"
        f"m 0 0 l {width} 0 {width} {bar_h} 0 {bar_h}"
    )
    for n, card in enumerate(groups):
        card_end = card[-1].end + HOLD
        if n + 1 < len(groups):
            card_end = min(card_end, groups[n + 1][0].start - 0.02)
        card_end = max(card_end, card[-1].end)
        placed, mid, half = layout(card, width, height, size, upper=vertical)
        start0 = card[0].start
        bar_w = 190 if vertical else 240
        by = mid + half + size * 0.3
        lines.append(
            f"Dialogue: 1,{stamp(start0)},{stamp(card_end)},Word,,0,0,0,,"
            f"{{\\an5\\pos({width / 2:.0f},{by:.0f})\\p1\\1c{accent}\\bord0\\shad0\\fscx0\\fad(0,100)"
            f"\\t(0,280,\\fscx100)}}m 0 0 l {bar_w} 0 {bar_w} 10 0 10"
        )
        for i, p in enumerate(placed):
            w = p.word
            text = clean(w.text.upper() if vertical else w.text)
            key = bool(re.search(r"[\d₹$%]", text))  # numbers and money keep the accent colour
            nxt = placed[i + 1].word.start if i + 1 < len(placed) else w.end
            hold_ms = max(int((nxt - w.start) * 1000), 120)
            fs = ass_size(text, p.size)
            big = 108 if key else 100
            colour = f"\\1c{accent}" if key else f"\\1c{accent}\\t({hold_ms},{hold_ms + 70},\\1c{ink})"
            tags = (
                f"\\an5\\move({p.x:.0f},{p.y + 34:.0f},{p.x:.0f},{p.y:.0f},0,170)\\fn{p.face}\\fs{fs}\\b1\\bord0\\shad0"
                f"{colour}\\fad(60,110)\\fscx84\\fscy84\\t(0,170,\\fscx{big + 5}\\fscy{big + 5})"
                f"\\t(170,290,\\fscx{big}\\fscy{big})"
            )
            lines.append(f"Dialogue: 2,{stamp(w.start - 0.02)},{stamp(card_end)},Word,,0,0,0,,{{{tags}}}{text}")
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
        "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Word,Noto Sans,{size},&H00{ink[2:-1]},&H00FFFFFF,&H00000000,"
        "&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    return header + "\n".join(lines) + "\n"
