"""ORM tables. Schema changes go through Alembic (migrations/), never create_all in production."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class UserRow(SQLModel, table=True):
    __tablename__ = "user"
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    created_at: datetime
    voice_md: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    daily_budget_usd: float = 0.0  # 0 = no limit


class WebSession(SQLModel, table=True):
    __tablename__ = "websession"
    id: str = Field(primary_key=True)  # sha256 of the cookie value
    user_id: int = Field(index=True)
    csrf: str
    expires_at: datetime


class OAuthState(SQLModel, table=True):
    __tablename__ = "oauthstate"
    state: str = Field(primary_key=True)
    purpose: str  # login | connect
    code_verifier: str
    binding: str  # sha256 of the browser's pre-auth nonce cookie
    user_id: int | None = None
    expires_at: datetime


class Channel(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: str
    thumb: str
    creds: str  # encrypted token blob (see token_vault.py)
    subscribers: int = 0
    views: int = 0
    videos: int = 0
    stats_at: datetime | None = None
    user_id: int | None = Field(default=None, index=True)
    status: str = "ok"
    uploads_playlist: str | None = None
    videos_synced_at: datetime | None = None


class Snapshot(SQLModel, table=True):
    """One row per channel per day, so the sidebar can show what changed since the last visit."""

    channel_id: str = Field(primary_key=True, index=True)
    day: date = Field(primary_key=True)
    subscribers: int
    views: int
    videos: int


class AnalyticsCache(SQLModel, table=True):
    channel_id: str = Field(primary_key=True)
    days: int = Field(primary_key=True)
    data: str
    fetched_at: datetime


class Settings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    stats_minutes: int = 5
    analytics_minutes: int = 60
    stats_enabled: bool = True
    analytics_enabled: bool = True
    currency: str = "USD"


class VideoRow(SQLModel, table=True):
    __tablename__ = "video"
    id: str = Field(primary_key=True)
    channel_id: str = Field(index=True)
    title: str
    published_at: datetime
    duration_s: int = 0
    views: int = 0
    likes: int = 0
    comments: int = 0
    avg_view_duration: float | None = None
    avg_view_pct: float | None = None
    ctr: float | None = None
    impressions: int | None = None
    retention: list[Any] | None = Field(default=None, sa_column=Column(JSON))
    fetched_at: datetime


class TrendVideo(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("channel_id", "day", "video_id"),)
    id: int | None = Field(default=None, primary_key=True)
    channel_id: str = Field(index=True)
    day: date
    video_id: str
    data: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    multiple: float = 0


class ScheduleRow(SQLModel, table=True):
    __tablename__ = "schedule"
    channel_id: str = Field(primary_key=True)
    enabled: bool = False
    timezone: str = "UTC"
    run_time: str = "07:00"
    days: str = "0123456"
    language: str = "en"
    region: str = "US"
    niche: str = ""
    custom_topics: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    target_minutes: int = 8
    shorts_enabled: bool = True
    next_run_utc: datetime | None = Field(default=None, index=True)
    confirmed: bool = False


class PipelineRun(SQLModel, table=True):
    __tablename__ = "pipeline_run"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    channel_id: str = Field(index=True)
    pipeline: str
    idem_key: str = Field(unique=True)
    status: str = "pending"
    inputs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class StepRun(SQLModel, table=True):
    __tablename__ = "step_run"
    __table_args__ = (UniqueConstraint("run_id", "step"),)
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    step: str
    status: str = "pending"
    output: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DeadLetter(SQLModel, table=True):
    __tablename__ = "dead_letter"
    id: int | None = Field(default=None, primary_key=True)
    run_id: int | None = Field(default=None, index=True)
    step: str | None = None
    task: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    error: str
    created_at: datetime
    resolved_at: datetime | None = None


class LLMCall(SQLModel, table=True):
    __tablename__ = "llm_call"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    run_id: int | None = None
    step: str = ""
    alias: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0
    latency_ms: int = 0
    cache_hit: bool = False
    template: str = ""
    template_version: str = ""
    created_at: datetime = Field(index=True)


class AssetRow(SQLModel, table=True):
    __tablename__ = "asset"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    channel_id: str = Field(index=True)
    run_id: int | None = Field(default=None, index=True)
    parent_id: int | None = None
    kind: str = Field(index=True)
    status: str = "needs_review"
    title: str = ""
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime
    updated_at: datetime


class MediaFile(SQLModel, table=True):
    __tablename__ = "media_file"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    channel_id: str = Field(index=True)
    video_id: str | None = Field(default=None, index=True)
    filename: str
    storage_key: str = ""
    size: int = 0
    received: int = 0
    status: str = "uploading"  # uploading | ready
    created_at: datetime
