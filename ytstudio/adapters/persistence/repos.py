"""Repositories + unit of work over SQLModel. Rows never leave this module; domain objects do."""

from __future__ import annotations

import builtins
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, delete, select

from ytstudio import ports
from ytstudio.adapters.persistence import tables as t
from ytstudio.adapters.persistence.db import session_for
from ytstudio.domain.errors import NotFound, PermanentError
from ytstudio.domain.models import (
    Asset,
    AssetStatus,
    Channel,
    ChannelStatus,
    MediaFile,
    RetentionPoint,
    Run,
    RunStatus,
    Schedule,
    SyncSettings,
    User,
    Video,
)
from ytstudio.ports import ChannelInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; everything we store is UTC."""
    return dt.replace(tzinfo=UTC) if dt and dt.tzinfo is None else dt


class _Repo:
    def __init__(self, s: Session):
        self.s = s


class SqlUsers(_Repo):
    @staticmethod
    def _dom(r: t.UserRow) -> User:
        assert r.id is not None
        return User(id=r.id, email=r.email, daily_budget_usd=r.daily_budget_usd)

    def get(self, user_id: int) -> User:
        if not (r := self.s.get(t.UserRow, user_id)):
            raise NotFound("user")
        return self._dom(r)

    def by_email(self, email: str) -> User | None:
        r = self.s.exec(select(t.UserRow).where(t.UserRow.email == email.lower())).first()
        return self._dom(r) if r else None

    def add(self, email: str) -> User:
        r = t.UserRow(email=email.lower(), created_at=utcnow())
        self.s.add(r)
        self.s.flush()
        return self._dom(r)

    def update(self, user: User) -> None:
        r = self.s.get(t.UserRow, user.id)
        if not r:
            raise NotFound("user")
        r.daily_budget_usd = user.daily_budget_usd
        self.s.add(r)

    def count(self) -> int:
        return self.s.exec(select(func.count()).select_from(t.UserRow)).one()

    def all(self) -> list[User]:
        return [self._dom(r) for r in self.s.exec(select(t.UserRow).order_by(col(t.UserRow.id)))]


class SqlChannels(_Repo):
    @staticmethod
    def _dom(r: t.Channel) -> Channel:
        return Channel(
            id=r.id,
            user_id=r.user_id,
            title=r.title,
            thumb=r.thumb,
            status=ChannelStatus(r.status),
            subscribers=r.subscribers,
            views=r.views,
            videos=r.videos,
            uploads_playlist=r.uploads_playlist,
            videos_synced_at=aware(r.videos_synced_at),
        )

    def _row(self, channel_id: str, user_id: int | None = None) -> t.Channel:
        r = self.s.get(t.Channel, channel_id)
        if not r or (user_id is not None and r.user_id != user_id):
            raise NotFound("channel")  # someone else's channel looks exactly like a missing one
        return r

    def get(self, channel_id: str, user_id: int | None = None) -> Channel:
        return self._dom(self._row(channel_id, user_id))

    def list(self, user_id: int | None = None) -> list[Channel]:
        q = select(t.Channel).order_by(t.Channel.title)
        if user_id is not None:
            q = q.where(t.Channel.user_id == user_id)
        return [self._dom(r) for r in self.s.exec(q)]

    def upsert(self, info: ChannelInfo, user_id: int) -> Channel:
        r = self.s.get(t.Channel, info.id)
        if r and r.user_id not in (None, user_id):
            raise PermanentError("This YouTube channel is already connected to another YTStudio account.")
        r = r or t.Channel(id=info.id, title=info.title, thumb=info.thumb, creds="")
        r.user_id, r.title, r.thumb, r.status = user_id, info.title, info.thumb, ChannelStatus.OK
        r.uploads_playlist = info.uploads_playlist or r.uploads_playlist
        self.s.add(r)
        self.s.flush()
        return self._dom(r)

    def set_status(self, channel_id: str, status: ChannelStatus) -> None:
        r = self._row(channel_id)
        r.status = status
        self.s.add(r)

    def save_stats(self, info: ChannelInfo, day: date) -> None:
        if not (r := self.s.get(t.Channel, info.id)):
            return  # removed while the sync was running
        r.title, r.thumb = info.title, info.thumb
        r.subscribers, r.views, r.videos, r.stats_at = info.subscribers, info.views, info.videos, utcnow()
        r.uploads_playlist = info.uploads_playlist or r.uploads_playlist
        self.s.add(r)
        self.s.merge(t.Snapshot(channel_id=r.id, day=day, subscribers=r.subscribers, views=r.views, videos=r.videos))

    def mark_videos_synced(self, channel_id: str, at: datetime) -> None:
        r = self._row(channel_id)
        r.videos_synced_at = at
        self.s.add(r)

    def delete(self, channel_id: str) -> None:
        self.s.exec(delete(t.Snapshot).where(col(t.Snapshot.channel_id) == channel_id))
        self.s.exec(delete(t.AnalyticsCache).where(col(t.AnalyticsCache.channel_id) == channel_id))
        self.s.exec(delete(t.ScheduleRow).where(col(t.ScheduleRow.channel_id) == channel_id))
        if r := self.s.get(t.Channel, channel_id):
            self.s.delete(r)

    def claim_unowned(self, user_id: int) -> int:
        rows = list(self.s.exec(select(t.Channel).where(col(t.Channel.user_id).is_(None))))
        for r in rows:
            r.user_id = user_id
            self.s.add(r)
        return len(rows)

    def save_report(self, channel_id: str, days: int, data: dict[str, Any]) -> None:
        self.s.merge(t.AnalyticsCache(channel_id=channel_id, days=days, data=json.dumps(data), fetched_at=utcnow()))

    def report(self, channel_id: str, days: int) -> tuple[dict[str, Any], datetime] | None:
        r = self.s.get(t.AnalyticsCache, (channel_id, days))
        return (json.loads(r.data), aware(r.fetched_at) or utcnow()) if r else None

    def last_stats_at(self) -> datetime | None:
        return aware(self.s.exec(select(func.max(t.Channel.stats_at))).first())

    def last_report_at(self) -> datetime | None:
        return aware(self.s.exec(select(func.max(t.AnalyticsCache.fetched_at))).first())


class SqlSyncSettings(_Repo):
    def _row(self) -> t.Settings:
        if not (r := self.s.get(t.Settings, 1)):
            r = t.Settings()
            self.s.add(r)
            self.s.flush()
        return r

    def get(self) -> SyncSettings:
        r = self._row()
        return SyncSettings(
            stats_minutes=r.stats_minutes,
            analytics_minutes=r.analytics_minutes,
            stats_enabled=r.stats_enabled,
            analytics_enabled=r.analytics_enabled,
            currency=r.currency,
        )

    def save(self, s: SyncSettings) -> None:
        r = self._row()
        for k, v in asdict(s).items():
            setattr(r, k, v)
        self.s.add(r)


class SqlMedia(_Repo):
    @staticmethod
    def _dom(r: t.MediaFile) -> MediaFile:
        assert r.id is not None
        return MediaFile(
            id=r.id,
            user_id=r.user_id,
            channel_id=r.channel_id,
            filename=r.filename,
            size=r.size,
            received=r.received,
            status=r.status,
            storage_key=r.storage_key,
            video_id=r.video_id,
        )

    def create(self, user_id: int, channel_id: str, filename: str, size: int) -> MediaFile:
        r = t.MediaFile(user_id=user_id, channel_id=channel_id, filename=filename, size=size, created_at=utcnow())
        self.s.add(r)
        self.s.flush()
        return self._dom(r)

    def get(self, media_id: int, user_id: int | None = None) -> MediaFile:
        r = self.s.get(t.MediaFile, media_id)
        if not r or (user_id is not None and r.user_id != user_id):
            raise NotFound("media")
        return self._dom(r)

    def update(self, m: MediaFile) -> None:
        r = self.s.get(t.MediaFile, m.id)
        if not r:
            raise NotFound("media")
        r.received, r.status, r.storage_key, r.video_id = m.received, m.status, m.storage_key, m.video_id
        self.s.add(r)

    def list(self, user_id: int, channel_id: str) -> list[MediaFile]:
        q = (
            select(t.MediaFile)
            .where(t.MediaFile.user_id == user_id, t.MediaFile.channel_id == channel_id)
            .order_by(col(t.MediaFile.id).desc())
        )
        return [self._dom(r) for r in self.s.exec(q)]

    def ready_for_videos(self, channel_id: str, video_ids: builtins.list[str]) -> builtins.list[MediaFile]:
        q = select(t.MediaFile).where(
            t.MediaFile.channel_id == channel_id,
            t.MediaFile.status == "ready",
            col(t.MediaFile.video_id).in_(video_ids),
        )
        return [self._dom(r) for r in self.s.exec(q)]


class SqlVideos(_Repo):
    @staticmethod
    def _dom(r: t.VideoRow) -> Video:
        return Video(
            id=r.id,
            channel_id=r.channel_id,
            title=r.title,
            published_at=aware(r.published_at) or utcnow(),
            views=r.views,
            likes=r.likes,
            comments=r.comments,
            duration_s=r.duration_s,
            avg_view_duration=r.avg_view_duration,
            avg_view_pct=r.avg_view_pct,
            ctr=r.ctr,
            impressions=r.impressions,
        )

    def upsert_many(self, videos: list[Video]) -> None:
        now = utcnow()
        for v in videos:
            r = self.s.get(t.VideoRow, v.id) or t.VideoRow(
                id=v.id, channel_id=v.channel_id, title=v.title, published_at=v.published_at, fetched_at=now
            )
            r.title, r.published_at, r.duration_s = v.title, v.published_at, v.duration_s
            r.views, r.likes, r.comments, r.fetched_at = v.views, v.likes, v.comments, now
            self.s.add(r)

    def recent(self, channel_id: str, since: datetime) -> list[Video]:
        q = (
            select(t.VideoRow)
            .where(t.VideoRow.channel_id == channel_id, t.VideoRow.published_at >= since)
            .order_by(col(t.VideoRow.published_at).desc())
        )
        return [self._dom(r) for r in self.s.exec(q)]

    def get(self, video_id: str) -> Video | None:
        r = self.s.get(t.VideoRow, video_id)
        return self._dom(r) if r else None

    def save_metrics(self, video_id: str, metrics: dict[str, float]) -> None:
        if not (r := self.s.get(t.VideoRow, video_id)):
            return
        r.avg_view_duration = metrics.get("averageViewDuration", r.avg_view_duration)
        r.avg_view_pct = metrics.get("averageViewPercentage", r.avg_view_pct)
        if "impressions" in metrics:
            r.impressions = int(metrics["impressions"])
        r.ctr = metrics.get("impressionsClickThroughRate", r.ctr)
        self.s.add(r)

    def save_retention(self, video_id: str, points: list[RetentionPoint]) -> None:
        if r := self.s.get(t.VideoRow, video_id):
            r.retention = [asdict(p) for p in points]
            self.s.add(r)

    def retention(self, video_id: str) -> list[RetentionPoint]:
        r = self.s.get(t.VideoRow, video_id)
        return [RetentionPoint(**p) for p in (r.retention or [])] if r else []


class SqlTrends(_Repo):
    def save(self, channel_id: str, day: date, rows: list[dict[str, Any]]) -> None:
        self.s.exec(delete(t.TrendVideo).where(t.TrendVideo.channel_id == channel_id, t.TrendVideo.day == day))  # type: ignore[arg-type]
        for row in rows:
            self.s.add(
                t.TrendVideo(
                    channel_id=channel_id, day=day, video_id=row["video_id"], data=row, multiple=row.get("multiple", 0)
                )
            )

    def for_day(self, channel_id: str, day: date | None = None) -> list[dict[str, Any]]:
        if day is None:
            day = self.s.exec(select(func.max(t.TrendVideo.day)).where(t.TrendVideo.channel_id == channel_id)).one()
            if day is None:
                return []
        q = (
            select(t.TrendVideo)
            .where(t.TrendVideo.channel_id == channel_id, t.TrendVideo.day == day)
            .order_by(col(t.TrendVideo.multiple).desc())
        )
        return [r.data | {"day": str(r.day)} for r in self.s.exec(q)]


class SqlSchedules(_Repo):
    @staticmethod
    def _dom(r: t.ScheduleRow) -> Schedule:
        hh, mm = (int(x) for x in r.run_time.split(":"))
        return Schedule(
            channel_id=r.channel_id,
            enabled=r.enabled,
            timezone=r.timezone,
            run_time=time(hh, mm),
            days=r.days,
            language=r.language,
            region=r.region,
            niche=r.niche,
            custom_topics=list(r.custom_topics or []),
            target_minutes=r.target_minutes,
            voice_md=r.voice_md,
            shorts_enabled=r.shorts_enabled,
            next_run_utc=aware(r.next_run_utc),
            confirmed=r.confirmed,
        )

    def get(self, channel_id: str) -> Schedule:
        r = self.s.get(t.ScheduleRow, channel_id)
        return self._dom(r) if r else Schedule(channel_id=channel_id)

    def save(self, schedule: Schedule) -> None:
        r = self.s.get(t.ScheduleRow, schedule.channel_id) or t.ScheduleRow(channel_id=schedule.channel_id)
        for k, v in asdict(schedule).items():
            setattr(r, k, v.strftime("%H:%M") if k == "run_time" else v)
        self.s.add(r)

    def claim_due(self, now: datetime, limit: int = 50) -> list[Schedule]:
        q = (
            select(t.ScheduleRow)
            .where(t.ScheduleRow.enabled == True, col(t.ScheduleRow.next_run_utc) <= now)  # noqa: E712
            .order_by(col(t.ScheduleRow.next_run_utc))
            .limit(limit)
            .with_for_update(skip_locked=True)
        )  # two dispatchers never pick the same row (Postgres)
        return [self._dom(r) for r in self.s.exec(q)]


class SqlRuns(_Repo):
    def _dom(self, r: t.PipelineRun) -> Run:
        assert r.id is not None
        steps = self.s.exec(select(t.StepRun).where(t.StepRun.run_id == r.id, t.StepRun.status == RunStatus.DONE))
        return Run(
            id=r.id,
            user_id=r.user_id,
            channel_id=r.channel_id,
            pipeline=r.pipeline,
            idem_key=r.idem_key,
            status=RunStatus(r.status),
            inputs=r.inputs,
            outputs={s.step: s.output or {} for s in steps},
        )

    def create(
        self, user_id: int, channel_id: str, pipeline: str, idem_key: str, inputs: dict[str, Any]
    ) -> tuple[Run, bool]:
        if existing := self.s.exec(select(t.PipelineRun).where(t.PipelineRun.idem_key == idem_key)).first():
            return self._dom(existing), False
        r = t.PipelineRun(
            user_id=user_id,
            channel_id=channel_id,
            pipeline=pipeline,
            idem_key=idem_key,
            inputs=inputs,
            created_at=utcnow(),
        )
        try:
            with self.s.begin_nested():
                self.s.add(r)
        except IntegrityError:  # lost a race with another dispatcher
            existing = self.s.exec(select(t.PipelineRun).where(t.PipelineRun.idem_key == idem_key)).one()
            return self._dom(existing), False
        return self._dom(r), True

    def get(self, run_id: int) -> Run:
        if not (r := self.s.get(t.PipelineRun, run_id)):
            raise NotFound("run")
        return self._dom(r)

    def set_status(self, run_id: int, status: RunStatus, error: str | None = None) -> None:
        r = self.s.get(t.PipelineRun, run_id)
        if not r:
            raise NotFound("run")
        r.status, r.error = status, error
        if status in (RunStatus.DONE, RunStatus.FAILED):
            r.finished_at = utcnow()
        self.s.add(r)

    def _step(self, run_id: int, step: str) -> t.StepRun:
        q = select(t.StepRun).where(t.StepRun.run_id == run_id, t.StepRun.step == step)
        return self.s.exec(q).first() or t.StepRun(run_id=run_id, step=step)

    def step_status(self, run_id: int, step: str) -> RunStatus | None:
        q = select(t.StepRun.status).where(t.StepRun.run_id == run_id, t.StepRun.step == step)
        status = self.s.exec(q).first()
        return RunStatus(status) if status else None

    def start_step(self, run_id: int, step: str) -> None:
        r = self._step(run_id, step)
        r.status, r.attempts, r.started_at, r.error = RunStatus.RUNNING, r.attempts + 1, utcnow(), None
        self.s.add(r)

    def finish_step(self, run_id: int, step: str, output: dict[str, Any]) -> None:
        r = self._step(run_id, step)
        r.status, r.output, r.finished_at = RunStatus.DONE, output, utcnow()
        self.s.add(r)

    def fail_step(self, run_id: int, step: str, error: str, status: RunStatus = RunStatus.FAILED) -> None:
        r = self._step(run_id, step)
        r.status, r.error, r.finished_at = status, error[:4000], utcnow()
        self.s.add(r)

    def dead_letter(self, run_id: int | None, step: str | None, task: str, payload: dict[str, Any], error: str) -> None:
        self.s.add(
            t.DeadLetter(run_id=run_id, step=step, task=task, payload=payload, error=error[:4000], created_at=utcnow())
        )

    def reset_failed(self, run_id: int) -> None:
        for r in self.s.exec(
            select(t.StepRun).where(
                t.StepRun.run_id == run_id, col(t.StepRun.status).in_([RunStatus.FAILED, RunStatus.WAITING])
            )
        ):
            r.status, r.error = RunStatus.PENDING, None
            self.s.add(r)
        for d in self.s.exec(
            select(t.DeadLetter).where(t.DeadLetter.run_id == run_id, col(t.DeadLetter.resolved_at).is_(None))
        ):
            d.resolved_at = utcnow()
            self.s.add(d)


class SqlAssets(_Repo):
    @staticmethod
    def _dom(r: t.AssetRow) -> Asset:
        return Asset(
            id=r.id,
            user_id=r.user_id,
            channel_id=r.channel_id,
            kind=r.kind,
            title=r.title,
            data=r.data,
            status=AssetStatus(r.status),
            run_id=r.run_id,
            parent_id=r.parent_id,
        )

    def add(self, asset: Asset) -> Asset:
        now = utcnow()
        r = t.AssetRow(
            user_id=asset.user_id,
            channel_id=asset.channel_id,
            run_id=asset.run_id,
            parent_id=asset.parent_id,
            kind=asset.kind,
            status=asset.status,
            title=asset.title,
            data=asset.data,
            created_at=now,
            updated_at=now,
        )
        self.s.add(r)
        self.s.flush()
        return self._dom(r)

    def get(self, asset_id: int, user_id: int) -> Asset:
        r = self.s.get(t.AssetRow, asset_id)
        if not r or r.user_id != user_id:
            raise NotFound("asset")
        return self._dom(r)

    def update(self, asset: Asset) -> None:
        r = self.s.get(t.AssetRow, asset.id)
        if not r or r.user_id != asset.user_id:
            raise NotFound("asset")
        r.title, r.data, r.status, r.updated_at = asset.title, dict(asset.data), asset.status, utcnow()
        self.s.add(r)

    def for_run(self, run_id: int) -> list[Asset]:
        return [
            self._dom(r)
            for r in self.s.exec(select(t.AssetRow).where(t.AssetRow.run_id == run_id).order_by(col(t.AssetRow.id)))
        ]


class SqlLLMCalls(_Repo):
    def record(self, **fields: Any) -> None:
        self.s.add(t.LLMCall(created_at=utcnow(), **fields))

    def spent_today(self, user_id: int, day: date) -> float:
        start = datetime.combine(day, time(0), tzinfo=UTC)
        q = select(func.coalesce(func.sum(t.LLMCall.cost_usd), 0.0)).where(
            t.LLMCall.user_id == user_id, t.LLMCall.created_at >= start
        )
        return float(self.s.exec(q).one())


class SqlUnitOfWork:
    users: ports.Users
    channels: ports.Channels
    videos: ports.Videos
    trends: ports.Trends
    schedules: ports.Schedules
    runs: ports.Runs
    assets: ports.Assets
    llm_calls: ports.LLMCalls
    sync_settings: ports.SyncSettingsRepo
    media: ports.Media

    def __init__(self, url: str):
        self.url = url

    def __enter__(self) -> SqlUnitOfWork:
        self.session = s = session_for(self.url)
        self.users, self.channels, self.videos = SqlUsers(s), SqlChannels(s), SqlVideos(s)
        self.trends, self.schedules, self.runs = SqlTrends(s), SqlSchedules(s), SqlRuns(s)
        self.assets, self.llm_calls = SqlAssets(s), SqlLLMCalls(s)
        self.sync_settings, self.media = SqlSyncSettings(s), SqlMedia(s)
        return self

    def __exit__(self, *exc: object) -> None:
        self.session.rollback()  # anything not committed is discarded
        self.session.close()

    def commit(self) -> None:
        self.session.commit()
