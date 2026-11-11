"""Entities and value objects. Pure Python: no I/O, no framework imports."""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any


class ChannelStatus(StrEnum):
    OK = "ok"
    RECONNECT_NEEDED = "reconnect_needed"


class AssetStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    DISCARDED = "discarded"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING = "waiting"  # rescheduled for quota reset


class Queue(StrEnum):
    IO = "io"
    LLM = "llm"
    MEDIA = "media"


class Effort(StrEnum):
    SHORT = "short"
    STANDARD = "standard"
    ANCHOR = "anchor"


@dataclass
class OAuthToken:
    refresh_token: str
    access_token: str | None = None
    expiry: datetime | None = None
    scopes: list[str] = field(default_factory=list)


@dataclass
class Channel:
    id: str
    user_id: int | None
    title: str
    thumb: str = ""
    status: ChannelStatus = ChannelStatus.OK
    subscribers: int = 0
    views: int = 0
    videos: int = 0
    uploads_playlist: str | None = None
    videos_synced_at: datetime | None = None


@dataclass
class Video:
    id: str
    channel_id: str
    title: str
    published_at: datetime
    views: int = 0
    likes: int = 0
    comments: int = 0
    duration_s: int = 0
    channel_title: str = ""
    avg_view_duration: float | None = None
    avg_view_pct: float | None = None
    ctr: float | None = None
    impressions: int | None = None

    @property
    def is_short(self) -> bool:
        return 0 < self.duration_s <= 180


@dataclass
class Cue:
    start: float
    end: float
    text: str


@dataclass
class RetentionPoint:
    ratio: float  # elapsedVideoTimeRatio, 0..1
    watch_ratio: float  # audienceWatchRatio
    relative: float | None = None  # relativeRetentionPerformance


@dataclass
class Schedule:
    channel_id: str
    enabled: bool = False
    timezone: str = "UTC"
    run_time: time = time(7, 0)
    days: str = "0123456"  # Monday=0
    language: str = "en"  # en | hi | hinglish
    region: str = "US"
    niche: str = ""
    custom_topics: list[str] = field(default_factory=list)
    target_minutes: int = 8
    shorts_enabled: bool = True
    next_run_utc: datetime | None = None
    confirmed: bool = False


@dataclass
class Asset:
    id: int | None
    user_id: int
    channel_id: str
    kind: str  # brief | idea | script | package | seo | short
    title: str
    data: dict[str, Any]
    status: AssetStatus = AssetStatus.NEEDS_REVIEW
    run_id: int | None = None
    parent_id: int | None = None


@dataclass
class Run:
    id: int
    user_id: int
    channel_id: str
    pipeline: str
    idem_key: str
    status: RunStatus
    inputs: dict[str, Any]
    outputs: dict[str, Any] = field(default_factory=dict)  # step name -> output of finished steps


@dataclass
class SyncSettings:
    stats_minutes: int = 5
    analytics_minutes: int = 60
    stats_enabled: bool = True
    analytics_enabled: bool = True
    currency: str = "USD"


@dataclass
class MediaFile:
    id: int
    user_id: int
    channel_id: str
    filename: str
    size: int
    received: int = 0
    status: str = "uploading"  # uploading | ready
    storage_key: str = ""
    video_id: str | None = None


@dataclass
class User:
    id: int
    email: str
    voice_md: str = ""
    daily_budget_usd: float = 2.0


def idem_key(channel_id: str, pipeline: str, local_day: date) -> str:
    return f"{channel_id}:{pipeline}:{local_day.isoformat()}"
