"""Use cases. They depend on ports and the domain only."""

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ytstudio.application.pipeline import PipelineRunner, StartResult
from ytstudio.application.schemas import ChannelProfileOut
from ytstudio.domain.errors import DomainError, NotFound, PermanentError, ReconnectNeeded
from ytstudio.domain.events import ChannelNeedsReconnect, Event
from ytstudio.domain.models import (
    AssetStatus,
    Channel,
    ChannelStatus,
    MediaFile,
    OAuthToken,
    Schedule,
    SyncSettings,
    User,
    idem_key,
)
from ytstudio.domain.schedule import local_day, next_run
from ytstudio.ports import (
    Analytics,
    Clock,
    LLMRequest,
    Notifier,
    OAuthProvider,
    TokenVault,
    UowFactory,
    VideoSource,
)

log = logging.getLogger(__name__)
PERIODS = (7, 28, 90, 365)


class NotAllowed(DomainError):
    pass


class Events:
    """Routes domain events to the notifiers of the user they belong to."""

    def __init__(self, uow: UowFactory, notifier: Notifier):
        self.uow, self.notifier = uow, notifier

    def publish(self, event: Event) -> None:
        with self.uow() as uow:
            user = uow.users.get(event.user_id)
        self.notifier.send(user, event)


class Accounts:
    def __init__(
        self, uow: UowFactory, oauth: OAuthProvider, vault: TokenVault, videos: VideoSource, allowed: set[str]
    ):
        self.uow, self.oauth, self.vault, self.videos, self.allowed = uow, oauth, vault, videos, allowed

    def login(self, email: str | None) -> User:
        if not email or email.lower() not in self.allowed:
            raise NotAllowed("This Google account is not on the ALLOWED_EMAILS list.")
        with self.uow() as uow:
            user = uow.users.by_email(email)
            if not user:
                user = uow.users.add(email)
                if uow.users.count() == 1:  # first user ever: channels connected before logins existed are theirs
                    uow.channels.claim_unowned(user.id)
            uow.commit()
        return user

    def connect(self, user_id: int, token: OAuthToken) -> Channel:
        info = self.videos.my_channel(token)
        if not info:
            raise NotFound("This Google account has no YouTube channel. Create one on youtube.com first.")
        with self.uow() as uow:
            channel = uow.channels.upsert(info, user_id)
            sched = uow.schedules.get(channel.id)
            uow.schedules.save(sched)  # creates the row with defaults; niche detection fills it in later
            uow.commit()
        if not token.refresh_token:  # Google only re-sends it with prompt=consent; keep the stored one
            try:
                token = replace(token, refresh_token=self.vault.load(channel.id).refresh_token)
            except DomainError:
                pass
        self.vault.save(channel.id, token)
        return channel

    def disconnect(self, user_id: int, channel_id: str) -> None:
        with self.uow() as uow:
            uow.channels.get(channel_id, user_id)
        try:
            self.oauth.revoke(self.vault.load(channel_id))
        except Exception:  # revoked already, or Google unreachable: still remove it locally
            log.warning("token revoke failed for %s", channel_id, exc_info=True)
        with self.uow() as uow:
            uow.channels.delete(channel_id)
            uow.commit()

    def mark_reconnect(self, channel_id: str, publish: Any) -> None:
        with self.uow() as uow:
            ch = uow.channels.get(channel_id)
            if ch.status == ChannelStatus.RECONNECT_NEEDED:
                return  # already flagged: notify once
            uow.channels.set_status(channel_id, ChannelStatus.RECONNECT_NEEDED)
            uow.commit()
        if ch.user_id:
            publish(ChannelNeedsReconnect(user_id=ch.user_id, channel_title=ch.title))


class Sync:
    """The pre-existing background syncs (sidebar stats, analytics), now behind ports."""

    def __init__(self, uow: UowFactory, videos: VideoSource, analytics: Analytics, clock: Clock):
        self.uow, self.videos, self.analytics, self.clock = uow, videos, analytics, clock

    def settings(self) -> SyncSettings:
        with self.uow() as uow:
            s = uow.sync_settings.get()
            uow.commit()
        return s

    def save_settings(self, s: SyncSettings, currencies: tuple[str, ...]) -> bool:
        s = SyncSettings(
            stats_minutes=min(max(s.stats_minutes, 1), 1440),
            analytics_minutes=min(max(s.analytics_minutes, 5), 1440),
            stats_enabled=s.stats_enabled,
            analytics_enabled=s.analytics_enabled,
            currency=s.currency if s.currency in currencies else "USD",
        )
        with self.uow() as uow:
            old = uow.sync_settings.get()
            uow.sync_settings.save(s)
            uow.commit()
        return old.currency != s.currency  # cached revenue is in the old currency

    def sync_stats(self) -> str:
        with self.uow() as uow:
            channels = uow.channels.list()
        healthy = [c for c in channels if c.status == ChannelStatus.OK]
        if not channels:
            return "No channels connected."
        ids = [c.id for c in channels]
        for using in healthy:  # public stats: any working login can read them all; skip broken ones
            try:
                infos = self.videos.channel_infos(ids, using=using.id)
                break
            except ReconnectNeeded:
                continue
        else:
            raise PermanentError("Every connected channel needs reconnecting.")
        today = self.clock.now().date()
        with self.uow() as uow:
            for info in infos.values():
                uow.channels.save_stats(info, today)
            uow.commit()
        return f"Updated {len(infos)} channel(s)."

    def sync_analytics(self) -> str:
        currency = self.settings().currency
        errors, done = [], 0
        with self.uow() as uow:
            channels = [c for c in uow.channels.list() if c.status == ChannelStatus.OK]
        for ch in channels:
            for days in PERIODS:
                try:
                    data = self.analytics.channel_report(ch.id, days, currency)
                except DomainError as e:  # one broken channel shouldn't stop the others
                    # Same error for every period: record it for all of them, don't burn quota repeating it.
                    data = {"error": str(e)}
                    errors.append(f"{ch.title}: {e}")
                with self.uow() as uow:
                    for d in PERIODS if "error" in data else (days,):
                        uow.channels.save_report(ch.id, d, data)
                    uow.commit()
                if "error" in data:
                    break
            else:
                done += 1
        if errors:
            raise PermanentError(" | ".join(errors))
        return f"Updated {done} channel(s), {len(PERIODS)} periods each."

    def sync_uploads(self, channel_id: str) -> list[str]:
        """Incremental own-video sync; returns ids first seen now."""
        now = self.clock.now()
        with self.uow() as uow:
            ch = uow.channels.get(channel_id)
        since = ch.videos_synced_at - timedelta(days=2) if ch.videos_synced_at else None
        vids = self.videos.uploads(channel_id, since=since, limit=200 if since else 400)
        with self.uow() as uow:
            new = [v.id for v in vids if uow.videos.get(v.id) is None]
            uow.videos.upsert_many(vids)
            uow.channels.mark_videos_synced(channel_id, now)
            uow.commit()
        return new if ch.videos_synced_at else []  # the very first sync is history, not "new uploads"

    def refresh_video_analytics(self, channel_id: str) -> int:
        now = self.clock.now()
        with self.uow() as uow:
            vids = uow.videos.recent(channel_id, now - timedelta(days=90))
        if not vids:
            return 0
        metrics = self.analytics.video_metrics(
            channel_id, [v.id for v in vids], (now - timedelta(days=90)).date(), now.date()
        )
        retention = {v.id: self.analytics.retention(channel_id, v.id) for v in vids[:10]}
        with self.uow() as uow:
            for vid, m in metrics.items():
                uow.videos.save_metrics(vid, m)
            for vid, points in retention.items():
                uow.videos.save_retention(vid, points)
            uow.commit()
        return len(metrics)


class Scheduling:
    def __init__(self, uow: UowFactory, runner: PipelineRunner, clock: Clock):
        self.uow, self.runner, self.clock = uow, runner, clock

    def save(self, user_id: int, schedule: Schedule) -> Schedule:
        try:
            ZoneInfo(schedule.timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise PermanentError(f"Unknown timezone {schedule.timezone!r}") from e
        schedule.days = "".join(sorted({d for d in schedule.days if d in "0123456"}))
        schedule.next_run_utc = (
            next_run(self.clock.now(), schedule.timezone, schedule.run_time, schedule.days)
            if schedule.enabled
            else None
        )
        schedule.confirmed = True
        with self.uow() as uow:
            uow.channels.get(schedule.channel_id, user_id)
            uow.schedules.save(schedule)
            uow.commit()
        return schedule

    def dispatch_due(self) -> list[StartResult]:
        """Called by the beat tick. Missed runs (worker down for days) catch up once, not once per day missed."""
        now = self.clock.now()
        with self.uow() as uow:
            due = uow.schedules.claim_due(now)  # row locks, SKIP LOCKED: two ticks never take the same row
            jobs = []
            for s in due:
                assert s.next_run_utc
                jobs.append((s.channel_id, local_day(s.next_run_utc, s.timezone)))
                s.next_run_utc = next_run(now, s.timezone, s.run_time, s.days)
                uow.schedules.save(s)
            owners = {c.id: c.user_id for c in uow.channels.list()}
            uow.commit()
        results = []
        for channel_id, day in jobs:
            owner = owners.get(channel_id)
            if owner is None:
                continue
            key = idem_key(channel_id, "daily", day)
            results.append(self.runner.start(owner, channel_id, "daily", key, {"trigger": "schedule"}))
        return results

    def run_now(self, user_id: int, channel_id: str) -> StartResult:
        with self.uow() as uow:
            s = uow.schedules.get(channel_id)
        day = local_day(self.clock.now(), s.timezone)
        return self.runner.start(
            user_id, channel_id, "daily", idem_key(channel_id, "daily", day), {"trigger": "manual"}
        )


class Profiles:
    """First connect: detect niche, language and region, prefilled for the user to confirm."""

    def __init__(self, uow: UowFactory, videos: VideoSource, llm: Any):
        self.uow, self.videos, self.llm = uow, videos, llm

    def detect(self, channel_id: str) -> Schedule:
        with self.uow() as uow:
            ch = uow.channels.get(channel_id)
            sched = uow.schedules.get(channel_id)
            titles = [v.title for v in uow.videos.recent(channel_id, datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC")))][:30]
        if sched.confirmed or ch.user_id is None:
            return sched  # never overwrite what the user confirmed
        info = self.videos.channel_infos([channel_id], using=channel_id).get(channel_id)
        out = self.llm.generate(
            LLMRequest(
                alias="fast",
                template="detect_niche",
                user_id=ch.user_id,
                step="detect_niche",
                variables={
                    "title": ch.title,
                    "description": info.description if info else "",
                    "country": info.country if info else "",
                    "default_language": info.default_language if info else "",
                    "titles": titles,
                },
            ),
            ChannelProfileOut,
        )
        sched.niche, sched.language, sched.region = out.niche, out.language, out.region.upper()[:2]
        sched.custom_topics = out.topics
        with self.uow() as uow:
            uow.schedules.save(sched)
            uow.commit()
        return sched


REGEN_STEP = {
    "idea": "generate_ideas",
    "script": "write_script",
    "package": "build_package",
    "seo": "write_seo",
    "brief": "analyze_performance",
}
EDITABLE = {
    "title",
    "angle",
    "why_now",
    "difference",
    "description",
    "tags",
    "pinned_comment",
    "target_queries",
    "pairs",
    "thumbnail_brief",
    "script",
    "first_line",
    "start",
    "end",
    "summary",
}


class Review:
    """Everything generated lands here as "needs review". Approve, edit, discard, regenerate. Never publish."""

    def __init__(self, uow: UowFactory, runner: PipelineRunner, clock: Clock):
        self.uow, self.runner, self.clock = uow, runner, clock

    def set_status(self, user_id: int, asset_id: int, status: AssetStatus) -> None:
        with self.uow() as uow:
            a = uow.assets.get(asset_id, user_id)
            a.status = status
            uow.assets.update(a)
            uow.commit()

    def edit(self, user_id: int, asset_id: int, changes: dict[str, Any]) -> None:
        bad = set(changes) - EDITABLE
        if bad:
            raise PermanentError(f"fields not editable: {sorted(bad)}")
        with self.uow() as uow:
            a = uow.assets.get(asset_id, user_id)
            a.data = a.data | changes
            a.title = str(changes.get("title", a.title))
            a.status = AssetStatus.NEEDS_REVIEW
            uow.assets.update(a)
            uow.commit()

    def regenerate(self, user_id: int, asset_id: int) -> StartResult:
        with self.uow() as uow:
            a = uow.assets.get(asset_id, user_id)
            seed = uow.runs.get(a.run_id).outputs if a.run_id else {}
        if a.kind not in REGEN_STEP:
            raise PermanentError(f"{a.kind} cannot be regenerated")
        step = REGEN_STEP[a.kind]
        inputs: dict[str, Any] = {"regenerate": True, "asset_id": a.id, "target_step": step, "seed_outputs": seed}
        if "idea" in a.data:
            inputs["idea"] = a.data["idea"]
        key = f"{a.channel_id}:regen:{a.id}:{int(self.clock.now().timestamp())}"
        return self.runner.start(user_id, a.channel_id, "regen", key, inputs, steps=[step, "save_section"])

    def develop(self, user_id: int, idea_asset_id: int) -> StartResult:
        with self.uow() as uow:
            a = uow.assets.get(idea_asset_id, user_id)
        if a.kind != "idea":
            raise PermanentError("only ideas can be developed")
        return self.runner.start(
            user_id, a.channel_id, "develop", f"{a.channel_id}:develop:{a.id}", {"idea": a.data, "idea_asset_id": a.id}
        )


class Shorts:
    def __init__(self, uow: UowFactory, runner: PipelineRunner):
        self.uow, self.runner = uow, runner

    def start(self, user_id: int, media_id: int) -> StartResult:
        with self.uow() as uow:
            m = uow.media.get(media_id, user_id)
        if m.status != "ready":
            raise PermanentError("the master file has not finished uploading")
        return self.runner.start(
            user_id,
            m.channel_id,
            "shorts",
            f"{m.channel_id}:shorts:{m.id}",
            {"media_id": m.id, "storage_key": m.storage_key, "video_id": m.video_id},
        )

    def trim(self, user_id: int, asset_id: int, start: float, end: float, now: datetime) -> StartResult:
        if not 15 <= end - start <= 58 or start < 0:
            raise PermanentError("A Short must be 15 to 58 seconds long.")
        with self.uow() as uow:
            a = uow.assets.get(asset_id, user_id)
            if a.kind != "short" or not a.run_id:
                raise PermanentError("only rendered Shorts can be trimmed")
            run = uow.runs.get(a.run_id)
        inputs = {
            "asset_id": a.id,
            "start": start,
            "end": end,
            "storage_key": run.inputs["storage_key"],
            "seed_outputs": {"transcribe_fallback": run.outputs["transcribe_fallback"]},
        }
        key = f"{a.channel_id}:trim:{a.id}:{int(now.timestamp())}"
        return self.runner.start(user_id, a.channel_id, "trim", key, inputs, steps=["rerender_short"])

    def on_new_uploads(self, channel_id: str, video_ids: list[str]) -> list[StartResult]:
        """Auto-trigger: a new upload was detected and its master render is already here."""
        if not video_ids:
            return []
        with self.uow() as uow:
            if not uow.schedules.get(channel_id).shorts_enabled:
                return []
            files: list[MediaFile] = uow.media.ready_for_videos(channel_id, video_ids)
        return [self.start(m.user_id, m.id) for m in files]
