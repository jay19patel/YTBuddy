"""Use cases. They depend on ports and the domain only."""

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ytstudio.application.pipeline import PipelineRunner, StartResult
from ytstudio.application.schemas import ChannelProfileOut, ChannelSetupOut
from ytstudio.domain.errors import DomainError, NotFound, PermanentError, ReconnectNeeded
from ytstudio.domain.events import ChannelNeedsReconnect, Event
from ytstudio.domain.metadata import clean_description, format_keywords
from ytstudio.domain.metrics import median_views, top_and_bottom
from ytstudio.domain.models import (
    Asset,
    AssetStatus,
    Channel,
    ChannelStatus,
    MediaFile,
    OAuthToken,
    Schedule,
    SyncSettings,
    User,
    format_of,
    idem_key,
)
from ytstudio.domain.schedule import local_day, next_run
from ytstudio.domain.video import LOOKS, VOICES
from ytstudio.ports import (
    Analytics,
    Clock,
    LLMRequest,
    Notifier,
    OAuthProvider,
    Storage,
    TokenVault,
    UowFactory,
    Uploader,
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

    def owner(self, email: str) -> User:
        """The user for this Gmail address. Only ALLOWED_EMAILS may sign in; an empty list means nobody."""
        email = email.lower().strip()
        if email not in self.allowed:
            raise NotAllowed(f"{email} is not allowed. Add it to ALLOWED_EMAILS in .env and restart.")
        with self.uow() as uow:
            user = uow.users.by_email(email)
            if not user:
                user = uow.users.add(email)
                if uow.users.count() == 1:  # first user: channels connected before logins existed are theirs
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

    def from_brief(self, user_id: int, channel_id: str, brief: str) -> tuple[Channel, Schedule]:
        """Draft the channel's About text, keywords and profile from a few plain words, using the channel's real
        uploads as evidence. Returns copies with the draft applied; nothing is saved until the owner saves."""
        brief = brief.strip()
        if len(brief) < 15:
            raise PermanentError("Write a few words about your channel first: what it is about and who it is for.")
        with self.uow() as uow:
            ch = uow.channels.get(channel_id, user_id)
            sched = uow.schedules.get(channel_id)
            vids = uow.videos.recent(channel_id, datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC")))
        longform = [v for v in vids if not v.is_short] or vids
        top, _ = top_and_bottom(longform, 5)
        out = self.llm.generate(
            LLMRequest(
                alias="smart",
                template="channel_setup",
                user_id=user_id,
                step="channel_setup",
                regenerate=True,  # pressing the button again should give a fresh take, not the cached one
                variables={
                    "channel": ch.title,
                    "brief": brief[:3000],
                    "current_about": ch.about,
                    "current_keywords": ch.keywords,
                    "niche": sched.niche,
                    "language": sched.language,
                    "region": sched.region,
                    "voice": sched.voice_md.strip(),
                    "titles": [v.title for v in vids][:30],
                    "median_views": int(median_views(longform)),
                    "top": [{"title": v.title, "views": v.views, "multiple": m} for v, m in top],
                },
            ),
            ChannelSetupOut,
        )
        return (
            replace(ch, about=clean_description(out.description), keywords=format_keywords(out.keywords)),
            replace(
                sched,
                niche=out.niche.strip()[:300],
                language=out.language,
                region=out.region.strip().upper()[:2] or sched.region,
                custom_topics=[t.strip() for t in out.topics if t.strip()][:8],
                target_minutes=min(max(out.target_minutes, 1), 120),
                voice_md=out.voice_md.strip()[:20000],
            ),
        )


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

    def __init__(self, uow: UowFactory, runner: PipelineRunner, clock: Clock, storage: Storage):
        self.uow, self.runner, self.clock, self.storage = uow, runner, clock, storage

    def set_status(self, user_id: int, asset_id: int, status: AssetStatus) -> None:
        with self.uow() as uow:
            a = uow.assets.get(asset_id, user_id)
            a.status = status
            uow.assets.update(a)
            uow.commit()

    def delete(self, user_id: int, asset_id: int) -> Asset:
        """Permanently delete an idea (with the script, package and SEO written for it), a script, package or SEO."""
        with self.uow() as uow:
            a = uow.assets.get(asset_id, user_id)
            if a.kind not in ("idea", "script", "package", "seo"):
                raise PermanentError(f"A {a.kind} cannot be deleted.")
            files = [
                k
                for x in (a, *uow.assets.children(asset_id, user_id))
                for k in (x.data.get("video_key"), x.data.get("audio_key"))
                if k
            ]
            uow.assets.delete(asset_id, user_id)
            uow.commit()
        for key in files:  # the voice-over and video made from a deleted script go with it
            self.storage.delete(key)
        return a

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
        if a.data.get("source") == "manual":  # regenerating would replace your words with a model's idea
            raise PermanentError("Your own idea is not regenerated. Edit it, or develop it into a script.")
        step = "write_short_script" if a.kind == "script" and format_of(a.data) == "short" else REGEN_STEP[a.kind]
        inputs: dict[str, Any] = {"regenerate": True, "asset_id": a.id, "target_step": step, "seed_outputs": seed}
        if "idea" in a.data:
            inputs["idea"] = a.data["idea"]
        key = f"{a.channel_id}:regen:{a.id}:{int(self.clock.now().timestamp())}"
        return self.runner.start(user_id, a.channel_id, "regen", key, inputs, steps=[step, "save_section"])

    def add_idea(
        self, user_id: int, channel_id: str, title: str, details: str = "", effort: str = "standard", format: str = ""
    ) -> Asset:
        """An idea you typed yourself, for this channel. Develop it into a script like any other idea."""
        title = " ".join(title.split())[:200]
        if not title:
            raise PermanentError("Write the idea first.")
        fmt = format if format in ("long", "short") else ("short" if effort == "short" else "long")
        idea = {
            "title": title,
            "angle": details.strip()[:2000],
            "why_now": "",
            "outlier_refs": [],
            "difference": "",
            "builds_on": [],
            "effort": "short" if fmt == "short" else (effort if effort in ("standard", "anchor") else "standard"),
            "format": fmt,
            "source": "manual",
        }
        with self.uow() as uow:
            uow.channels.get(channel_id, user_id)  # owner check
            added = uow.assets.add(
                Asset(id=None, user_id=user_id, channel_id=channel_id, kind="idea", title=title, data=idea)
            )
            uow.commit()
        return added

    def make_video(self, user_id: int, script_asset_id: int, voice: str = "", look: str = "") -> StartResult:
        """Voice-over and subtitle video for one script. Pressing it again makes a new one that replaces the old."""
        with self.uow() as uow:
            a = uow.assets.get(script_asset_id, user_id)
        if a.kind != "script":
            raise PermanentError("Only a script can be turned into a video.")
        inputs: dict[str, Any] = {"asset_id": a.id}
        if voice in {v.id for v in VOICES}:
            inputs["voice"] = voice
        if look in {x.id for x in LOOKS}:
            inputs["look"] = look
        key = f"{a.channel_id}:video:{a.id}:{int(self.clock.now().timestamp())}"
        return self.runner.start(user_id, a.channel_id, "script_video", key, inputs)

    def develop(self, user_id: int, idea_asset_id: int) -> StartResult:
        with self.uow() as uow:
            a = uow.assets.get(idea_asset_id, user_id)
        if a.kind != "idea":
            raise PermanentError("only ideas can be developed")
        pipeline = "develop_short" if format_of(a.data) == "short" else "develop"
        return self.runner.start(
            user_id, a.channel_id, pipeline, f"{a.channel_id}:develop:{a.id}", {"idea": a.data, "idea_asset_id": a.id}
        )


PRIVACY = ("private", "unlisted", "public")


class Shorts:
    def __init__(self, uow: UowFactory, runner: PipelineRunner, youtube: Uploader, storage: Storage):
        self.uow, self.runner, self.youtube, self.storage = uow, runner, youtube, storage

    def publish(
        self,
        user_id: int,
        asset_id: int,
        title: str,
        description: str,
        tags: list[str],
        privacy: str,
        publish_at: datetime | None,
        now: datetime,
    ) -> str:
        """Upload a rendered Short, or the video made from a script (a Short or a long video), when the user presses
        the button. Returns the YouTube video id."""
        with self.uow() as uow:
            a = uow.assets.get(asset_id, user_id)
        if a.kind not in ("short", "script") or not a.data.get("video_key"):
            raise PermanentError("Only a rendered Short, or a script with its video made, can be uploaded.")
        if a.data.get("youtube_id"):
            raise PermanentError(f"This video is already on YouTube: https://youtu.be/{a.data['youtube_id']}")
        if privacy not in PRIVACY:
            raise PermanentError("Pick private, unlisted or public.")
        is_short = a.kind == "short" or format_of(a.data) == "short"
        title = (title.strip() or str(a.data.get("title") or a.title))[:100]
        description = description.strip()
        if is_short and "#shorts" not in (title + description).lower():
            description = f"{description}\n\n#Shorts".strip()  # YouTube uses the tag to classify the video as a Short
        status: dict[str, Any] = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
        if publish_at:  # uploaded as private now, YouTube makes it public at that time
            status = {**status, "privacyStatus": "private", "publishAt": publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")}
        body = {
            "snippet": {"title": title, "description": description, "tags": tags[:15], "categoryId": "22"},
            "status": status,
        }
        video_id = self.youtube.upload(a.channel_id, str(self.storage.get(a.data["video_key"])), body)
        with self.uow() as uow:
            a = uow.assets.get(asset_id, user_id)
            a.status = AssetStatus.APPROVED
            if a.kind == "short":  # a script keeps the idea's name; the video's own title is stored below
                a.title = title
            a.data = {
                **a.data,
                "youtube_title": title,
                "youtube_description": description,
                "youtube_id": video_id,
                "uploaded_at": now.isoformat(),
                "privacy": status["privacyStatus"],
                "publish_at": publish_at.isoformat() if publish_at else None,
            }
            uow.assets.update(a)
            uow.commit()
        return video_id

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

    def delete_master(self, user_id: int, media_id: int) -> None:
        """Remove an uploaded master file and its stored copy. Shorts already made from it stay."""
        with self.uow() as uow:
            m = uow.media.get(media_id, user_id)
            if uow.runs.is_active(f"{m.channel_id}:shorts:{m.id}"):
                raise PermanentError(
                    "Shorts are being made from this file. Wait for that run to finish, then delete it."
                )
            uow.media.delete(m.id, user_id)
            uow.commit()
        if m.storage_key:
            self.storage.delete(m.storage_key)

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
