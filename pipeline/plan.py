"""Storyboard + timed words → timeline.json.

A storyboard (YAML) is a list of scenes; each scene has an ordered `content` list. An entry whose key is a text role
(lead, hero, sub, caption) or `card` is spoken: it takes its words, in order, from words.json and every word appears on
its own spoken frame. Any other entry (sticker, ui_window, tag_pill, …) is not spoken; it appears with the next spoken
entry unless it says `at`. Times in a storyboard:

    at: 1.5        seconds after the scene starts       at: "@edit"    when "edit" is said in this scene
    at: "+0.4"     seconds after this entry's own time  at: "@edit#2"  the second "edit" of the scene
    at: "#3"       when the scene's 4th word (index 3) is said
    until: …       same forms; when the element leaves  clear: true    earlier text leaves when this entry arrives
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from pipeline import markup, segment
from pipeline.schema import TEXT_TYPES, Background, Element, Scene, Timeline, VideoInfo, Word
from pipeline.theme import EMOJI
from pipeline.transcribe import TimedWord, norm

log = logging.getLogger(__name__)

ROLES = ("lead", "hero", "sub", "caption")
SPOKEN = (*ROLES, "card")
MAIN_PROP = {
    "card": "words", "tag_pill": "label", "sticker": "name", "typing": "text", "dm_card": "text", "ui_window": "kind",
    "button_state": "variant", "outro": "next_label", "character": "side",
}  # fmt: skip
TIME_KEYS = {"at", "highlight_at", "text_at", "tap_at", "approved_at", "cut_at", "logo_at"}
MODIFIERS = {"at", "until", "clear"}
LEAD_IN = 0.12  # a scene cuts in just before its first word so the word's animation lands on the voice
HOLD = 0.5  # how long the last frame holds after the final word
ELEMENT = TypeAdapter(Element)
TYPES = {get_args(c.model_fields["type"].annotation)[0] for c in get_args(get_args(Element)[0])}


class StoryScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bg: str | dict[str, str] = "black"
    layout: str = "left_text"
    bg_word: str | None = None
    deco: str | None = None
    transition: str = "cut"
    watermark: str = "top_right"
    start: float | None = None  # reference time; only used to fake word times for the demo
    duration: float | None = None  # for a scene with no spoken words (an outro)
    content: list[dict[str, Any]] = Field(default_factory=list)


class Story(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenes: list[StoryScene] = Field(min_length=1)


def load_story(path: Path) -> Story:
    try:
        return Story.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except yaml.YAMLError as e:
        raise ValueError(f"{path} is not valid YAML: {e}") from e


def kind_of(entry: dict[str, Any]) -> str:
    keys = [k for k in entry if k not in MODIFIERS and (k in SPOKEN or k in TYPES)]
    if len(keys) != 1:
        raise ValueError(
            f"storyboard entry needs exactly one type key ({', '.join(SPOKEN)} or an element type): {entry}"
        )
    return keys[0]


def spoken_text(entry: dict[str, Any]) -> str | None:
    k = kind_of(entry)
    if k not in SPOKEN:
        return None
    v = entry[k]
    return str(v["text"] if isinstance(v, dict) else v)


def script_of(story: Story) -> list[markup.Token]:
    """Every spoken token of the storyboard, in order: the script the voice must match."""
    return [
        t for s in story.scenes for e in s.content if (txt := spoken_text(e)) is not None for t in markup.parse(txt)
    ]


def plan(story: Story, words: list[TimedWord], video: VideoInfo, total_s: float) -> Timeline:
    tokens = script_of(story)
    if len(tokens) != len(words):
        raise ValueError(
            f"the storyboard has {len(tokens)} spoken words but words.json has {len(words)}; "
            "align the timings to the storyboard's text (the CLI does this when you pass --storyboard)"
        )
    # which words each scene speaks
    spans: list[range] = []
    i = 0
    for s in story.scenes:
        n = sum(len(markup.parse(t)) for e in s.content if (t := spoken_text(e)) is not None)
        spans.append(range(i, i + n))
        i += n
    starts: list[float] = []
    for k, span in enumerate(spans):
        prev_end = words[spans[k - 1][-1]].end if k and spans[k - 1] else (starts[-1] if starts else 0.0)
        when = 0.0 if k == 0 else words[span[0]].start - LEAD_IN if span else prev_end + HOLD
        starts.append(max(when, starts[-1] + 1 / video.fps if starts else 0.0))
    last = story.scenes[-1]
    end_all = max(total_s, starts[-1] + (last.duration or 0), (words[-1].end + HOLD) if words else 0)
    ends = [*starts[1:], end_all]
    scenes = [
        _scene(f"s{k + 1:02d}", s, starts[k], ends[k], words[span.start : span.stop], tokens[span.start : span.stop])
        for k, (s, span) in enumerate(zip(story.scenes, spans, strict=True))
    ]
    video = video.model_copy(update={"duration_frames": max(1, round(end_all * video.fps))})
    return Timeline(video=video, scenes=scenes)


def _scene(
    sid: str, s: StoryScene, start: float, end: float, words: list[TimedWord], tokens: list[markup.Token]
) -> Scene:
    def anchor(v: Any, base: float) -> float:
        if isinstance(v, int | float):
            return start + float(v)
        v = str(v)
        if v.startswith("+"):
            return base + float(v[1:])
        if m := re.fullmatch(r"#(\d+)", v):
            if int(m.group(1)) >= len(words):
                raise ValueError(f"scene {sid}: word index {v} but the scene has {len(words)} words")
            return words[int(m.group(1))].start
        if m := re.fullmatch(r"@(.+?)(?:#(\d+))?", v):
            hits = [w.start for w in words if norm(w.word) == norm(m.group(1))]
            n = int(m.group(2) or 1)
            if len(hits) < n:
                raise ValueError(f"scene {sid}: no {v!r} among its words: {' '.join(w.word for w in words)}")
            return hits[n - 1]
        raise ValueError(f"scene {sid}: time {v!r} is not seconds, '+seconds' or '@word'")

    def resolve(obj: Any, base: float) -> Any:
        if isinstance(obj, dict):
            return {
                k: anchor(v, base) if k in TIME_KEYS and v is not None else resolve(v, base) for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [resolve(x, base) for x in obj]
        return obj

    # when each spoken entry starts, so unspoken entries can attach to the next one
    cursor, spoken_at = 0, []
    for e in s.content:
        txt = spoken_text(e)
        n = len(markup.parse(txt)) if txt is not None else 0
        spoken_at.append(words[cursor].start if n else None)
        cursor += n
    elements: list[dict[str, Any]] = []
    cursor = 0
    for i, e in enumerate(s.content):
        k = kind_of(e)
        txt = spoken_text(e)
        if txt is not None:
            n = len(markup.parse(txt))
            ws = [
                Word(text=t.text, start=w.start, end=w.end, style=t.style, color=t.color)
                for t, w in zip(tokens[cursor : cursor + n], words[cursor : cursor + n], strict=True)
            ]
            cursor += n
            at = ws[0].start
            extra = {x: y for x, y in (e[k].items() if isinstance(e[k], dict) else []) if x != "text"}
            extra |= {x: y for x, y in e.items() if x not in MODIFIERS and x != k}
            el: dict[str, Any] = (
                {"type": "text_line", "props": {"role": k, "words": ws, **extra}}
                if k in ROLES
                else {"type": "card", "props": {"words": ws, **extra}}
            )
        else:
            later = [t for t in spoken_at[i:] if t is not None]
            earlier = [t for t in spoken_at[:i] if t is not None]
            at = anchor(e["at"], start) if "at" in e else later[0] if later else earlier[-1] if earlier else start
            v = e[k]
            props = dict(v) if isinstance(v, dict) else ({MAIN_PROP[k]: v} if k in MAIN_PROP and v is not None else {})
            props |= {x: y for x, y in e.items() if x not in MODIFIERS and x != k}
            el = {"type": k, "props": resolve(props, at)}
        el |= {"start": round(max(at, start), 3), "end": round(anchor(e["until"], at), 3) if "until" in e else None}
        if e.get("clear"):
            for prev in elements:
                if prev["end"] is None and _is_flow(prev):
                    prev["end"] = el["start"]
        if k == "ui_window":
            for prev in elements:
                if prev["type"] == "ui_window" and prev["end"] is None:
                    prev["end"] = el["start"]
        elements.append(el)
    bg = s.bg if isinstance(s.bg, dict) else {"color": s.bg}
    return Scene(
        id=sid,
        start=round(start, 3),
        end=round(end, 3),
        background=Background.model_validate(bg),
        bg_word=s.bg_word,
        deco_text=s.deco,
        transition_in=s.transition,  # type: ignore[arg-type]
        layout=s.layout,  # type: ignore[arg-type]
        watermark_pos=s.watermark,  # type: ignore[arg-type]
        elements=[ELEMENT.validate_python(x) for x in elements],
    )


def _is_flow(el: dict[str, Any]) -> bool:
    return el["type"] in (*TEXT_TYPES, "tag_pill", "list_reveal", "swatches") and el["props"].get("role") != "caption"


def missing_assets(t: Timeline, assets: Path) -> list[str]:
    """Every file the timeline refers to that is not in assets/."""
    names: set[str] = set()
    for s in t.scenes:
        for e in s.elements:
            p = e.props.model_dump()
            if e.type == "sticker":
                names.add(f"stickers/{p['name']}.png")
            names |= {f"stickers/{it['icon']}.png" for it in p.get("items", []) if it.get("icon")}
            if p.get("image"):
                names.add(p["image"])
    names |= {x for x in (t.video.brand.avatar, t.video.brand.character) if x}
    names |= {f"stickers/{n}.png" for n in EMOJI.values()}
    return sorted(n for n in names if not (assets / n).exists())


# ---------------------------------------------------------------- auto mode: no storyboard

BACKGROUNDS = ["black", "orange", "blue", "beige", "crimson", "beige", "charcoal", "blue"]
STICKER_HINTS = {
    "second": "stopwatch", "minute": "stopwatch", "time": "stopwatch", "jaldi": "stopwatch", "folder": "folder",
    "file": "folder", "edit": "scissors", "video": "clapper", "camera": "camera", "idea": "brain", "samajh": "brain",
    "dekh": "eyes", "dekho": "eyes", "secret": "lock", "magic": "wand", "style": "palette", "growth": "chart_up",
    "grow": "chart_up", "paisa": "chart_up", "paise": "chart_up", "money": "chart_up", "profit": "chart_up",
    "tool": "hammer", "setup": "gear", "laptop": "laptop", "browser": "globe", "prompt": "key", "tip": "key",
    "perfect": "hundred", "comment": "thinking", "kyun": "question", "kaise": "question", "follow": "heart",
    "like": "thumbs_up", "galti": "cross", "mistake": "cross", "waste": "cross", "python": "laptop", "code": "laptop",
}  # fmt: skip
FALLBACK_STICKERS = ["sparkles", "fire", "eyes", "hundred", "brain", "key"]
FILLER = {"hai", "hain", "ka", "ki", "ke", "ko", "se", "me", "mein", "aur", "yeh", "woh", "toh", "bhi", "the", "and"}


def _emphasise(chunk: list[markup.Token], box: bool) -> list[markup.Token]:
    """Numbers bold; the longest real word gets a highlight box (once per scene) when the script marked nothing."""
    out = [
        markup.Token(t.text, "bold") if t.style == "normal" and any(c.isdigit() for c in t.text) else t for t in chunk
    ]
    if box and all(t.style == "normal" for t in chunk):
        cands = [i for i, t in enumerate(chunk) if len(norm(t.text)) >= 4 and norm(t.text) not in FILLER]
        if cands:
            i = max(cands, key=lambda i: len(norm(chunk[i].text)))
            out[i] = markup.Token(chunk[i].text, "highlight")
    return out


def marked(t: markup.Token) -> str:
    """A token back in storyboard markup."""
    if t.style == "normal":
        return t.text
    a, b = next((a, b) for a, b, st in markup.MARKERS if st == t.style)
    return f"{a}{t.text}{'|' + t.color if t.color else ''}{b}"


def auto_story(
    tokens: list[markup.Token], words: list[TimedWord], min_s: float, max_s: float, character: bool
) -> Story:
    """A storyboard from rules: a scene per 3-8 s, colours cycling, lead/hero/sub lines with the key word boxed,
    a short keyword in the background and a sticker per scene (one a word suggests, else a neutral one)."""
    scenes: list[StoryScene] = []
    last_sticker = ""
    for n, idx in enumerate(segment.scenes(words, min_s, max_s)):
        toks = [tokens[i] for i in idx]
        span = words[idx[-1]].end - words[idx[0]].start
        short = span < 2.5 and len(toks) <= 6
        content: list[dict[str, Any]] = []
        if short:
            content.append({"hero": " ".join(marked(t) for t in _emphasise(toks, False))})
        else:
            chunks = [toks[:2], toks[2:7], toks[7:]] if len(toks) > 6 else [toks]
            for role, ch in zip(("lead", "hero", "sub") if len(chunks) == 3 else ("hero",), chunks, strict=False):
                if ch:
                    content.append({role: " ".join(marked(t) for t in _emphasise(ch, role == "hero"))})
        hints = [(j, STICKER_HINTS[k]) for j, t in enumerate(toks) for k in STICKER_HINTS if norm(t.text).startswith(k)]
        hint = next((h for h in hints if h[1] != last_sticker), None)  # never the same sticker twice in a row
        layout = "center_bracket" if short else "text_plus_character" if character and n % 3 == 2 else "left_text"
        if not short:  # every scene gets one sticker, so the visual half of the frame is never empty
            spare = [x for x in FALLBACK_STICKERS if x != last_sticker]
            name = last_sticker = hint[1] if hint else spare[n % len(spare)]
            at = f"#{hint[0]}" if hint else f"#{min(len(toks) - 1, 2)}"
            x = 0.25 if layout == "text_plus_character" else 0.68
            content.append({"sticker": name, "at": at, "x": x, "y": 0.35, "size": 1.3})
        words_ok = [norm(t.text) for t in toks if 3 <= len(norm(t.text)) <= 7 and norm(t.text) not in FILLER]
        keyword = max(words_ok, key=len) if words_ok else None  # short enough to read when huge
        bg = "black" if short else BACKGROUNDS[n % len(BACKGROUNDS)]
        if layout == "text_plus_character":
            content.append({"character": "right"})
        scenes.append(
            StoryScene(
                bg=bg,
                layout=layout,
                bg_word=keyword if layout == "left_text" else None,
                transition="crossfade" if bg == "black" and n else "cut",
                watermark="top_right",
                content=content,
            )
        )
    return Story(scenes=scenes)


class LlmLine(BaseModel):
    role: str = Field(pattern="^(lead|hero|sub)$")
    first_word: int = Field(ge=0)


class LlmSticker(BaseModel):
    name: str
    word: int = Field(ge=0)
    x: float = Field(0.7, ge=0, le=1)
    y: float = Field(0.3, ge=0, le=1)


class LlmScene(BaseModel):
    first_word: int = Field(ge=0)
    bg: str
    layout: str = Field(pattern="^(left_text|center_bracket|text_plus_character)$")
    bg_word: str | None = None
    lines: list[LlmLine] = Field(min_length=1)
    stickers: list[LlmSticker] = Field(default_factory=list, max_length=3)


class LlmPlan(BaseModel):
    scenes: list[LlmScene] = Field(min_length=1)


def llm_story(tokens: list[markup.Token], words: list[TimedWord], model: str, stickers: list[str]) -> Story:
    """An LLM picks scenes, colours, layouts and stickers from a fixed menu; its JSON is validated, never trusted."""
    import litellm

    from pipeline.theme import PALETTE

    listing = "\n".join(f"{i}\t{w.start:.2f}\t{t.text}" for i, (t, w) in enumerate(zip(tokens, words, strict=True)))
    prompt = (
        "You plan a kinetic-typography explainer video. Words (index, start seconds, text):\n"
        f"{listing}\n\nSplit them into scenes of 3-8 seconds starting on sentence boundaries. For each scene give "
        "first_word, bg (one of: black, orange, blue, beige, crimson, charcoal), layout (left_text, center_bracket for "
        "short dramatic lines on black, text_plus_character), optional bg_word (one keyword of the scene), lines (role "
        "lead|hero|sub with first_word; lines split the scene's words in order) and up to 2 stickers (name, word index "
        f"when it pops, x, y between 0 and 1). Sticker names: {', '.join(stickers)}. "
        'Reply with JSON only: {"scenes": [...]}'
    )
    reply = litellm.completion(model=model, messages=[{"role": "user", "content": prompt}], temperature=0)
    text = reply.choices[0].message.content or ""
    data = LlmPlan.model_validate(json.loads(text[text.find("{") : text.rfind("}") + 1]))
    firsts = [s.first_word for s in data.scenes] + [len(tokens)]
    if firsts[0] != 0 or firsts != sorted(set(firsts)):
        raise ValueError("scenes must start at word 0 and move forward")
    scenes: list[StoryScene] = []
    for s, nxt in zip(data.scenes, firsts[1:], strict=True):
        if s.bg not in PALETTE:
            raise ValueError(f"unknown background {s.bg}")
        cuts = [ln.first_word for ln in s.lines] + [nxt]
        if cuts[0] != s.first_word or cuts != sorted(set(cuts)):
            raise ValueError("lines must cover the scene's words in order")
        content: list[dict[str, Any]] = [
            {ln.role: " ".join(marked(t) for t in tokens[a:b])}
            for ln, a, b in zip(s.lines, cuts, cuts[1:], strict=False)
        ]
        for st in s.stickers:
            if st.name not in stickers or not s.first_word <= st.word < nxt:
                raise ValueError(f"bad sticker {st}")
            content.append({"sticker": st.name, "at": f"#{st.word - s.first_word}", "x": st.x, "y": st.y})
        scenes.append(StoryScene(bg=s.bg, layout=s.layout, bg_word=s.bg_word, content=content))
    return Story(scenes=scenes)
