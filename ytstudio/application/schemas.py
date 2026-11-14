"""Structured LLM outputs. Bump VERSION when a schema changes: it is part of the LLM cache key."""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field


class ChannelProfileOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    niche: str = Field(description="One line, specific: who it is for and what about")
    language: Literal["en", "hi", "hinglish"]
    region: str = Field(description="ISO 3166-1 alpha-2 country code of the main audience")
    topics: list[str] = Field(max_length=8, description="Short search phrases people type for this niche")


class ChannelSetupOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    description: str = Field(description="The channel About text, at most 900 characters")
    keywords: list[str] = Field(description="10 to 20 short channel keywords")
    niche: str
    language: Literal["en", "hi", "hinglish"]
    region: str = Field(description="2-letter country code of the main audience")
    topics: list[str] = Field(description="Up to 8 phrases people search for")
    target_minutes: int
    voice_md: str = Field(description="Markdown voice profile that ends with a channel analysis")


class PerformanceOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    summary: str
    working: list[str] = Field(max_length=5, description="Patterns shared by the top performers, citing video ids")
    not_working: list[str] = Field(max_length=5, description="Patterns shared by the bottom performers")
    retention_notes: list[str] = Field(default_factory=list, max_length=5)


class IdeaOut(BaseModel):
    title: str
    angle: str
    why_now: str = Field(description="Why this is timely, tied to the outlier videos listed in outlier_refs")
    outlier_refs: list[str] = Field(description="video_id values from the outlier list only")
    difference: str = Field(description="What makes this different from those outliers")
    builds_on: list[str] = Field(default_factory=list, description="video_id values of the creator's own videos")
    effort: Literal["short", "standard", "anchor"]


class IdeasOut(BaseModel):
    VERSION: ClassVar[str] = "2"
    ideas: list[IdeaOut] = Field(min_length=1, max_length=10, description="Exactly the number asked for, best first")


class HooksOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    hooks: list[str] = Field(min_length=5, max_length=5, description="Five spoken opening lines, one formula each")


class ScriptSection(BaseModel):
    heading: str
    spoken: str = Field(description="The exact words to say")
    on_screen_text: list[str] = Field(default_factory=list)
    beat: Literal["hook", "turn", "body", "pattern_interrupt", "retention_beat", "payoff", "outro"]


class ScriptOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    title_working: str
    sections: list[ScriptSection] = Field(min_length=3)
    estimated_minutes: float


class ShortScriptOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    hook: str = Field(description="The first spoken line, said in the first two seconds")
    sections: list[ScriptSection] = Field(min_length=2, max_length=5)
    title: str = Field(max_length=100)
    description: str = Field(description="Two short lines, no hashtags")
    estimated_seconds: int = Field(description="Spoken length, at most 58")


class PackagePair(BaseModel):
    title: str = Field(max_length=100)
    thumbnail_text: str = Field(max_length=40)


class PackageOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    pairs: list[PackagePair] = Field(min_length=3, max_length=3)
    thumbnail_brief: str = Field(description="Subject, expression, composition, colours, what the text says")


class SeoOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    description: str
    tags: list[str] = Field(max_length=15)
    pinned_comment: str
    target_queries: list[str] = Field(min_length=3, max_length=3)


class ShortPick(BaseModel):
    window: int = Field(description="Index of the candidate window you picked")
    start: float
    end: float
    first_line: str = Field(description="A new opening line, spoken or on screen, that makes it self-contained")
    reason: str


class ShortPicksOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    picks: list[ShortPick] = Field(max_length=5)


class ShortMeta(BaseModel):
    title: str = Field(max_length=100)
    description: str


class ShortMetaOut(BaseModel):
    VERSION: ClassVar[str] = "1"
    shorts: list[ShortMeta]
