"""What is in assets/, found by file name every time a video is planned, so a new or changed file is used at once with
nothing to configure. The file name is what the storyboard (and the LLM) calls it, so name files for what they show.

    stickers/<name>.png                       a 3D sticker: fire.png, money.png, rocket.png
    characters/<who>/<expression>.png         a presenter's faces: neutral.png, happy.png, shocked.png, thinking.png
    characters/<who>/<expression>-talk.png    the same picture with the mouth open: the presenter lip-syncs to the voice
    backgrounds/<name>.jpg                    a full-frame picture behind a scene: office.jpg, city_night.webp
    sfx/<name>.wav                            pop, whoosh, click, typing; <sticker>.wav or <expression>.wav play with it
    music/<track>.mp3                         a music bed under the voice, a different one per video
"""

from pathlib import Path

IMAGES = (".png", ".webp", ".jpg", ".jpeg", ".svg")
TALK = "-talk"
DEFAULT_FACES = ("neutral", "default", "normal", "happy", "smile")


def _images(root: Path, rel: str) -> dict[str, str]:
    """name → path inside assets/ for every picture in assets/<rel> (hidden files skipped)."""
    folder = root / rel
    if not folder.is_dir():
        return {}
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGES and not p.name.startswith("."))
    return {p.stem: f"{rel}/{p.name}" for p in files}


def stickers(root: Path) -> list[str]:
    return sorted(p.stem for p in (root / "stickers").glob("*.png"))  # the thumbnail drawer reads PNG only


def backgrounds(root: Path) -> dict[str, str]:
    return _images(root, "backgrounds")


def characters(root: Path) -> dict[str, dict[str, str]]:
    """who → {expression: path}, mouth-open "-talk" pictures included under their own names."""
    folder = root / "characters"
    if not folder.is_dir():
        return {}
    dirs = [d for d in sorted(folder.iterdir()) if d.is_dir() and not d.name.startswith(".")]
    found = {d.name: _images(root, f"characters/{d.name}") for d in dirs}
    return {who: faces for who, faces in found.items() if any(not f.endswith(TALK) for f in faces)}


def faces(root: Path) -> dict[str, list[str]]:
    """who → the expressions a storyboard can ask for (the mouth-open pictures are used on their own)."""
    return {who: [f for f in fs if not f.endswith(TALK)] for who, fs in characters(root).items()}


def face(root: Path, who: str | None, expression: str | None) -> tuple[str, str | None] | None:
    """(picture, mouth-open picture or None) for a presenter's expression. An unknown presenter is the first one and
    an unknown expression the presenter's default face, so a wrong name never loses the presenter. None when
    assets/characters is empty."""
    cast = characters(root)
    if not cast:
        return None
    pics = cast.get(who or "") or next(iter(cast.values()))
    names = {f.lower(): f for f in pics if not f.endswith(TALK)}
    want = (expression or "").strip().lower().replace(" ", "_")
    name = names.get(want) or next((names[f] for f in DEFAULT_FACES if f in names), next(iter(names.values())))
    return pics[name], pics.get(name + TALK)
