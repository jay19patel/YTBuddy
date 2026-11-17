"""One thumbnail standard for every upload: the LLM's design (words, boxed word, colour, sticker) drawn in the videos'
look. Used by the script-video pipeline and by the Publish page."""

import tempfile
from pathlib import Path
from typing import Any

from ytstudio.config import ROOT
from ytstudio.ports import Clock, ImageRenderer, Storage

STICKERS = ROOT / "assets" / "stickers"
PLAIN = {"text": "", "highlight": "", "bg": "blue", "sticker": "sparkles"}


def stickers() -> list[str]:
    return sorted(p.stem for p in STICKERS.glob("*.png"))  # new files are used at once


def finish(design: dict[str, Any], text: str, title: str, suggested: list[str]) -> dict[str, Any]:
    """The creator's own text wins; a missing text, boxed word or unknown sticker is filled in so it always draws."""
    out = {**PLAIN, **design}
    out["text"] = (text or out["text"] or (suggested[0] if suggested else " ".join(title.split()[:4])))[:40]
    words = out["text"].split()
    if not out["highlight"] or out["highlight"].lower() not in out["text"].lower():
        out["highlight"] = max(words, key=len) if words else ""
    if out["sticker"] not in stickers():
        out["sticker"] = "sparkles"
    return out


def draw(images: ImageRenderer, storage: Storage, clock: Clock, design: dict[str, Any], short: bool, name: str) -> str:
    """Draw the design and store it as thumbs/<name>-<time>.jpg. Returns the storage key."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "thumb.jpg"
        sticker = STICKERS / f"{design['sticker']}.png"
        images.thumbnail(design["text"], design["highlight"], design["bg"], sticker, short, out)
        return storage.put(out, f"thumbs/{name}-{int(clock.now().timestamp() * 1000)}.jpg")  # a new URL each time
