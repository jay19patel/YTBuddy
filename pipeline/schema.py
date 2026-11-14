"""timeline.json: what the Remotion renderer draws. Times are seconds from the start of the video; the renderer turns
them into frames with `frame_of`. These models are the single source: `python -m pipeline gen` writes the matching
TypeScript types to renderer/src/types.gen.ts."""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from pipeline.theme import PALETTE

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def frame_of(seconds: float, fps: int) -> int:
    """The frame a moment falls on. The renderer uses the same rule (Math.round)."""
    return round(seconds * fps)


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_serialization_defaults_required=True
    )  # an LLM inventing fields is an error, not silently ignored


def color(v: str) -> str:
    if v in PALETTE or HEX.match(v):
        return v
    raise ValueError(f"unknown colour {v!r}: use a palette name ({', '.join(PALETTE)}) or #RRGGBB")


Color = Annotated[str, AfterValidator(color), Field(description="palette name or #RRGGBB")]
Style = Literal["normal", "bold", "accent", "highlight", "strike", "serif"]


class Word(Model):
    text: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    style: Style = "normal"
    color: Color | None = None


class Fonts(Model):
    sans: str = "Inter Tight"
    serif: str = "Playfair Display"
    display: str = "Bebas Neue"


class Brand(Model):
    handle: str
    avatar: str | None = None  # path inside assets/
    character: str | None = None
    palette: dict[str, str] = Field(default_factory=lambda: dict(PALETTE))
    fonts: Fonts = Field(default_factory=Fonts)


class VideoInfo(Model):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0, le=120)
    duration_frames: int = Field(gt=0)
    brand: Brand


class Background(Model):
    kind: Literal["color", "split", "diagonal"] = "color"
    color: Color
    panel: Color | None = None  # the second colour of a split or diagonal background


class Item(Model):
    """A row of a list, window, tree, storyboard or set of bubbles. `at` is when it appears (seconds)."""

    label: str
    at: float | None = None
    icon: str | None = None  # a sticker name from assets/stickers
    sub: str | None = None  # second line: a comment's text, a tool's description
    depth: int = 0
    highlight_at: float | None = None
    color: Color | None = None


class Base(Model):
    start: float = Field(ge=0)
    end: float | None = None  # None: stays until the scene ends


class TextLineProps(Model):
    role: Literal["lead", "hero", "sub", "caption"] = "hero"
    words: list[Word] = Field(min_length=1)
    color: Color | None = None


class TextLine(Base):
    type: Literal["text_line"] = "text_line"
    props: TextLineProps


class CardProps(Model):
    words: list[Word] = Field(min_length=1)
    color: Color = "crimson"
    enter: Literal["bottom", "left", "right"] = "bottom"


class Card(Base):
    type: Literal["card"] = "card"
    props: CardProps


class TagPillProps(Model):
    label: str
    color: Color = "yellow"
    text_color: Color = "ink"


class TagPill(Base):
    type: Literal["tag_pill"] = "tag_pill"
    props: TagPillProps


class StickerProps(Model):
    name: str
    x: float = Field(0.7, ge=0, le=1)  # position of the centre inside the scene's visual area
    y: float = Field(0.3, ge=0, le=1)
    size: float = Field(1.0, gt=0, le=4)  # times the theme's sticker size
    rotate: float = 0
    cross: bool = False  # a red ✕ over it ("this won't work in the browser")


class Sticker(Base):
    type: Literal["sticker"] = "sticker"
    props: StickerProps


class UiWindowProps(Model):
    kind: Literal["app", "finder", "chat", "tools", "note", "tree", "drop"]
    title: str = ""
    items: list[Item] = Field(default_factory=list)
    text: str = ""  # chat: the prompt that is typed or pasted
    text_at: float | None = None
    paste: bool = False  # the whole text arrives at once with a ⌘V badge
    image: str | None = None  # app: a screenshot inside assets/


class UiWindow(Base):
    type: Literal["ui_window"] = "ui_window"
    props: UiWindowProps


class ListRevealProps(Model):
    items: list[Item] = Field(min_length=1)


class ListReveal(Base):
    type: Literal["list_reveal"] = "list_reveal"
    props: ListRevealProps


class TypingProps(Model):
    text: str
    placeholder: str = ""
    paste: bool = False
    heart: bool = False  # a like heart pops when the typing is done


class Typing(Base):
    type: Literal["typing"] = "typing"
    props: TypingProps


class CommentBubblesProps(Model):
    items: list[Item] = Field(min_length=1)  # label = username, sub = comment


class CommentBubbles(Base):
    type: Literal["comment_bubbles"] = "comment_bubbles"
    props: CommentBubblesProps


class ButtonStateProps(Model):
    variant: Literal["follow", "approve", "reject"]
    tap_at: float | None = None


class ButtonState(Base):
    type: Literal["button_state"] = "button_state"
    props: ButtonStateProps


class TimelineCutProps(Model):
    blocks: int = Field(9, ge=2, le=30)
    cuts: list[int] = Field(default_factory=lambda: [1, 4, 6])  # which blocks drop out
    cut_at: float | None = None


class TimelineCut(Base):
    type: Literal["timeline_cut"] = "timeline_cut"
    props: TimelineCutProps


class StoryboardProps(Model):
    items: list[Item] = Field(min_length=1)


class Storyboard(Base):
    type: Literal["storyboard"] = "storyboard"
    props: StoryboardProps


class DraftCardsProps(Model):
    count: int = Field(3, ge=1, le=5)
    approved_at: float | None = None


class DraftCards(Base):
    type: Literal["draft_cards"] = "draft_cards"
    props: DraftCardsProps


class DmCardProps(Model):
    text: str
    chips: list[str] = Field(default_factory=list)


class DmCard(Base):
    type: Literal["dm_card"] = "dm_card"
    props: DmCardProps


class SwatchesProps(Model):
    colors: list[Color] = Field(default_factory=lambda: ["black", "orange", "blue", "beige", "crimson"])
    shape: Literal["dots", "dashes"] = "dots"


class Swatches(Base):
    type: Literal["swatches"] = "swatches"
    props: SwatchesProps


class CharacterProps(Model):
    side: Literal["right", "left"] = "right"


class Character(Base):
    type: Literal["character"] = "character"
    props: CharacterProps = Field(default_factory=lambda: CharacterProps())


class AvatarBadgeProps(Model):
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.2, ge=0, le=1)


class AvatarBadge(Base):
    type: Literal["avatar_badge"] = "avatar_badge"
    props: AvatarBadgeProps = Field(default_factory=lambda: AvatarBadgeProps.model_validate({}))


class OutroProps(Model):
    next_label: str = "day 07"
    logo_at: float | None = None


class Outro(Base):
    type: Literal["outro"] = "outro"
    props: OutroProps = Field(default_factory=lambda: OutroProps())


Element = Annotated[
    TextLine
    | Card
    | TagPill
    | Sticker
    | UiWindow
    | ListReveal
    | Typing
    | CommentBubbles
    | ButtonState
    | TimelineCut
    | Storyboard
    | DraftCards
    | DmCard
    | Swatches
    | Character
    | AvatarBadge
    | Outro,
    Field(discriminator="type"),
]
TEXT_TYPES = ("text_line", "card")  # elements whose words are spoken


class Scene(Model):
    id: str
    start: float = Field(ge=0)
    end: float
    background: Background
    bg_word: str | None = None
    deco_text: str | None = None  # the tiny vertical text column near the right edge
    transition_in: Literal["cut", "diagonal_wipe", "split_slide", "crossfade"] = "cut"
    layout: Literal["left_text", "center_bracket", "text_plus_window", "text_plus_character", "split_panel", "outro"]
    watermark_pos: Literal["top_right", "mid_left", "none"] = "top_right"
    elements: list[Element] = Field(default_factory=list)

    @model_validator(mode="after")
    def _times(self) -> "Scene":
        if self.end <= self.start:
            raise ValueError(f"scene {self.id}: end {self.end} is not after start {self.start}")
        return self


class Timeline(Model):
    video: VideoInfo
    scenes: list[Scene] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> "Timeline":
        for a, b in zip(self.scenes, self.scenes[1:], strict=False):
            if b.start < a.end - 1e-6:
                raise ValueError(f"scene {b.id} starts at {b.start}, before scene {a.id} ends at {a.end}")
        if len({s.id for s in self.scenes}) != len(self.scenes):
            raise ValueError("scene ids must be unique")
        return self
