"""Ports: the interfaces the application layer depends on. Adapters implement them."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from ytstudio.domain.events import Event
from ytstudio.domain.models import (
    Asset,
    Channel,
    ChannelStatus,
    Cue,
    MediaFile,
    OAuthToken,
    RetentionPoint,
    Run,
    RunStatus,
    Schedule,
    SyncSettings,
    User,
    Video,
)

T = TypeVar("T", bound=BaseModel)


# ---------- Google ----------


@dataclass
class OAuthStart:
    url: str
    state: str
    code_verifier: str


@dataclass
class OAuthResult:
    token: OAuthToken
    email: str | None = None


class OAuthProvider(Protocol):
    def start(self, scopes: list[str], login_hint: str | None = None) -> OAuthStart: ...
    def finish(self, scopes: list[str], callback_url: str, state: str, code_verifier: str) -> OAuthResult: ...
    def revoke(self, token: OAuthToken) -> None: ...


@dataclass
class ChannelInfo:
    id: str
    title: str
    thumb: str
    uploads_playlist: str = ""
    description: str = ""
    keywords: str = ""  # brandingSettings.channel.keywords: space separated, multi-word ones quoted
    country: str = ""
    default_language: str = ""
    subscribers: int = 0
    views: int = 0
    videos: int = 0


class VideoSource(Protocol):
    """Data API reads: own channel, own videos, captions, other channels' public data."""

    def my_channel(self, token: OAuthToken) -> ChannelInfo | None: ...
    def channel_infos(self, channel_ids: list[str], using: str | None) -> dict[str, ChannelInfo]: ...
    def uploads(self, channel_id: str, since: datetime | None, limit: int = 200) -> list[Video]: ...
    def videos(self, video_ids: list[str], using: str | None, critical: bool = True) -> list[Video]: ...
    def recent_views(self, channel_ids: list[str], using: str | None) -> dict[str, list[int]]: ...
    def captions(self, channel_id: str, video_id: str) -> list[Cue] | None: ...
    def update_about(self, channel_id: str, description: str, keywords: str) -> None: ...


class Analytics(Protocol):
    def channel_report(self, channel_id: str, days: int, currency: str) -> dict[str, Any]: ...
    def video_metrics(
        self, channel_id: str, video_ids: list[str], start: date, end: date
    ) -> dict[str, dict[str, float]]: ...
    def retention(self, channel_id: str, video_id: str) -> list[RetentionPoint]: ...


@dataclass
class TrendQuery:
    channel_id: str
    region: str
    language: str
    niche: str
    topics: list[str]
    category_id: str | None = None


class TrendProvider(Protocol):
    name: str

    def fetch(self, q: TrendQuery) -> list[Video]: ...


class QuotaBudget(Protocol):
    def spend(self, units: int, critical: bool) -> None: ...  # raises QuotaBudgetBlocked / QuotaExceeded
    def exhausted(self) -> None: ...
    def used_today(self) -> int: ...


# ---------- LLM ----------


@dataclass
class LLMRequest:
    alias: str  # fast | smart | long
    template: str
    variables: dict[str, Any]
    user_id: int
    run_id: int | None = None
    step: str = ""
    critical: bool = True
    regenerate: bool = False


class LLM(Protocol):
    def generate(self, req: LLMRequest, schema: type[T]) -> T: ...


@dataclass
class PromptTemplate:
    name: str
    version: str
    system: str
    user: str


class LLMSettingsStore(Protocol):
    """Models and API keys saved in the database. scope is "global" or "user:<id>"; keys are stored encrypted."""

    def keys(self, scope: str) -> dict[str, str]: ...
    def set_key(self, scope: str, provider: str, key: str) -> None: ...
    def delete_key(self, scope: str, provider: str) -> None: ...
    def aliases(self, scope: str) -> dict[str, list[str]]: ...
    def set_aliases(self, scope: str, aliases: dict[str, list[str]]) -> None: ...  # an empty list removes it


class PromptRepository(Protocol):
    def get(self, name: str) -> PromptTemplate: ...
    def render(self, name: str, variables: dict[str, Any]) -> tuple[PromptTemplate, str, str]: ...


# ---------- media ----------


class Transcriber(Protocol):
    def transcribe(self, media_path: Path, language: str | None) -> list[Cue]: ...


class Narrator(Protocol):
    def speak(self, lines: list[tuple[str, float]], voice: str, out: Path) -> tuple[list[dict[str, Any]], str]:
        """Read each (text, pause after in seconds) aloud into one WAV at `out`. Returns, per line, when it is said
        ({"start", "end", "words": [{"text", "start", "end"}]}, words may be empty) and which voice engine did it."""
        ...


class MediaProcessor(Protocol):
    def duration(self, path: Path) -> float: ...
    def encode_audio(self, wav: Path, out: Path) -> None: ...
    def render_script_video(self, units: list[dict[str, Any]], audio: Path, vertical: bool, out: Path) -> None: ...
    def render_short(self, src: Path, start: float, end: float, cues: list[Cue], out: Path) -> None: ...
    def frame(self, src: Path, at: float, out: Path) -> None: ...


class ImageRenderer(Protocol):
    def cover(self, frame: Path, text: str, out: Path) -> None: ...


class Uploader(Protocol):
    def upload(self, channel_id: str, path: str, body: dict[str, Any]) -> str: ...  # returns the YouTube video id


class Storage(Protocol):
    def put(self, local: Path, key: str) -> str: ...
    def get(self, key: str) -> Path: ...  # a local path you can read (downloaded if remote)
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


# ---------- scorers (vendored youtube-agent-skill tools) ----------


class HookScorer(Protocol):
    def score(self, hooks: list[str]) -> list[dict[str, Any]]: ...


class PackageLinter(Protocol):
    def lint(self, title: str, thumb_text: str | None) -> dict[str, Any]: ...


class OutlierRanker(Protocol):
    def rank(self, videos: list[dict[str, Any]], min_multiple: float) -> dict[str, Any]: ...


class ChapterBuilder(Protocol):
    def build(self, cues: list[Cue], target: int = 7) -> dict[str, Any]: ...


class EditDecisionListBuilder(Protocol):
    def build(self, cues: list[Cue]) -> dict[str, Any]: ...


class RetentionAnalyzer(Protocol):
    def analyze(
        self, points: list[RetentionPoint], duration_s: float, cues: list[Cue] | None = None
    ) -> dict[str, Any]: ...


# ---------- infrastructure ----------


class TokenVault(Protocol):
    def save(self, channel_id: str, token: OAuthToken) -> None: ...
    def load(self, channel_id: str) -> OAuthToken: ...


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_s: int) -> None: ...


class DistributedLock(Protocol):
    def acquire(self, key: str, ttl_s: int, owner: str) -> bool: ...
    def release(self, key: str, owner: str) -> bool: ...


class RateLimiter(Protocol):
    def acquire(self, bucket: str, rate_per_s: float, capacity: int, timeout_s: float = 30) -> bool: ...


class Notifier(Protocol):
    def send(self, user: User, event: Event) -> None: ...


@dataclass
class StepPlan:
    name: str
    queue: str
    fan_out: bool = False


class TaskDispatcher(Protocol):
    def start_pipeline(self, run_id: int, steps: list[StepPlan], idem_key: str) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


# ---------- persistence ----------


class Users(Protocol):
    def get(self, user_id: int) -> User: ...
    def by_email(self, email: str) -> User | None: ...
    def add(self, email: str) -> User: ...
    def update(self, user: User) -> None: ...
    def count(self) -> int: ...
    def all(self) -> list[User]: ...


class Channels(Protocol):
    def get(self, channel_id: str, user_id: int | None = None) -> Channel: ...  # raises NotFound
    def list(self, user_id: int | None = None) -> list[Channel]: ...
    def upsert(self, info: ChannelInfo, user_id: int) -> Channel: ...
    def set_status(self, channel_id: str, status: ChannelStatus) -> None: ...
    def save_stats(self, info: ChannelInfo, day: date) -> None: ...
    def set_about(self, channel_id: str, about: str, keywords: str) -> None: ...
    def mark_videos_synced(self, channel_id: str, at: datetime) -> None: ...
    def delete(self, channel_id: str) -> None: ...
    def claim_unowned(self, user_id: int) -> int: ...
    def save_report(self, channel_id: str, days: int, data: dict[str, Any]) -> None: ...
    def report(self, channel_id: str, days: int) -> tuple[dict[str, Any], datetime] | None: ...
    def last_stats_at(self) -> datetime | None: ...
    def last_report_at(self) -> datetime | None: ...


class SyncSettingsRepo(Protocol):
    def get(self) -> SyncSettings: ...
    def save(self, s: SyncSettings) -> None: ...


class Media(Protocol):
    def create(self, user_id: int, channel_id: str, filename: str, size: int) -> MediaFile: ...
    def get(self, media_id: int, user_id: int | None = None) -> MediaFile: ...
    def update(self, m: MediaFile) -> None: ...
    def delete(self, media_id: int, user_id: int) -> None: ...
    def list(self, user_id: int, channel_id: str) -> list[MediaFile]: ...
    def ready_for_videos(self, channel_id: str, video_ids: builtins.list[str]) -> builtins.list[MediaFile]: ...


class Videos(Protocol):
    def upsert_many(self, videos: list[Video]) -> None: ...
    def recent(self, channel_id: str, since: datetime) -> list[Video]: ...
    def get(self, video_id: str) -> Video | None: ...
    def save_metrics(self, video_id: str, metrics: dict[str, float]) -> None: ...
    def save_retention(self, video_id: str, points: list[RetentionPoint]) -> None: ...
    def retention(self, video_id: str) -> list[RetentionPoint]: ...


class Trends(Protocol):
    def save(self, channel_id: str, day: date, rows: list[dict[str, Any]]) -> None: ...
    def for_day(self, channel_id: str, day: date | None = None) -> list[dict[str, Any]]: ...


class Schedules(Protocol):
    def get(self, channel_id: str) -> Schedule: ...
    def save(self, schedule: Schedule) -> None: ...
    def claim_due(self, now: datetime, limit: int = 50) -> list[Schedule]: ...  # FOR UPDATE SKIP LOCKED


class Runs(Protocol):
    def create(
        self, user_id: int, channel_id: str, pipeline: str, idem_key: str, inputs: dict[str, Any]
    ) -> tuple[Run, bool]: ...
    def get(self, run_id: int) -> Run: ...
    def is_active(self, idem_key: str) -> bool: ...
    def set_status(self, run_id: int, status: RunStatus, error: str | None = None) -> None: ...
    def step_status(self, run_id: int, step: str) -> RunStatus | None: ...
    def start_step(self, run_id: int, step: str) -> None: ...
    def finish_step(self, run_id: int, step: str, output: dict[str, Any]) -> None: ...
    def fail_step(self, run_id: int, step: str, error: str, status: RunStatus = RunStatus.FAILED) -> None: ...
    def dead_letter(
        self, run_id: int | None, step: str | None, task: str, payload: dict[str, Any], error: str
    ) -> None: ...
    def reset_failed(self, run_id: int) -> None: ...


class Assets(Protocol):
    def add(self, asset: Asset) -> Asset: ...
    def get(self, asset_id: int, user_id: int) -> Asset: ...
    def children(self, asset_id: int, user_id: int) -> list[Asset]: ...
    def update(self, asset: Asset) -> None: ...
    def for_run(self, run_id: int) -> list[Asset]: ...
    def delete(self, asset_id: int, user_id: int) -> int: ...  # also removes what was written for it


class LLMCalls(Protocol):
    def record(self, **fields: Any) -> None: ...
    def spent_today(self, user_id: int, day: date) -> float: ...


class UnitOfWork(Protocol):
    users: Users
    channels: Channels
    videos: Videos
    trends: Trends
    schedules: Schedules
    runs: Runs
    assets: Assets
    llm_calls: LLMCalls
    sync_settings: SyncSettingsRepo
    media: Media

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, *exc: object) -> None: ...
    def commit(self) -> None: ...


UowFactory = Callable[[], UnitOfWork]


@dataclass
class Deps:
    """Everything a pipeline step may use. Built once by the container."""

    uow: UowFactory
    clock: Clock
    videos: VideoSource
    analytics: Analytics
    trend_providers: list[TrendProvider]
    llm: LLM
    prompts: PromptRepository
    hooks: HookScorer
    linter: PackageLinter
    outliers: OutlierRanker
    chapters: ChapterBuilder
    edl: EditDecisionListBuilder
    retention: RetentionAnalyzer
    transcriber: Transcriber
    narrator: Narrator
    media: MediaProcessor
    images: ImageRenderer
    storage: Storage
    publish: Callable[[Event], None]
    settings: dict[str, Any] = field(default_factory=dict)
