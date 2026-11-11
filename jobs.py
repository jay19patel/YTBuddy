"""Background sync: pulls YouTube data into SQLite on a schedule so pages only read the database."""

import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import func, select

from db import AnalyticsCache, Channel, Snapshot, get_session, get_settings
from youtube_api import PERIODS, fetch_analytics, fetch_stats, friendly_error

log = logging.getLogger("ytstudio.jobs")
scheduler = BackgroundScheduler(timezone=timezone.utc)


def now() -> datetime:
    return datetime.now(timezone.utc)


def sync_stats() -> str:
    channels = [c.id for c in all_channels()]
    if not channels:
        return "No channels connected."
    found = fetch_stats(channels, using=channels[0])
    with get_session() as db:
        for cid, item in found.items():
            c = db.get(Channel, cid)
            st, sn = item["statistics"], item["snippet"]
            c.title, c.thumb = sn["title"], sn["thumbnails"]["default"]["url"]
            c.subscribers, c.views, c.videos = (int(st.get(k, 0)) for k in ("subscriberCount", "viewCount", "videoCount"))
            c.stats_at = now()
            db.add(c)
            db.merge(Snapshot(channel_id=cid, day=date.today(), subscribers=c.subscribers, views=c.views, videos=c.videos))
        db.commit()
    return f"Updated {len(found)} channel(s)."


def sync_analytics() -> str:
    currency = get_settings().currency
    errors, done = [], 0
    for ch in all_channels():
        for days in PERIODS:
            try:
                data = fetch_analytics(ch.id, days, currency)
            except Exception as e:  # one broken channel shouldn't stop the others
                # Same error for every period: record it for all of them, don't burn quota repeating it.
                data = {"error": friendly_error(e)}
                errors.append(f"{ch.title}: {data['error']}")
            with get_session() as db:
                for d in PERIODS if "error" in data else (days,):
                    db.merge(AnalyticsCache(channel_id=ch.id, days=d, data=json.dumps(data), fetched_at=now()))
                db.commit()
            if "error" in data:
                break
        else:
            done += 1
    if errors:
        raise RuntimeError(" | ".join(errors))
    return f"Updated {done} channel(s), {len(PERIODS)} periods each."


JOBS = {
    "sync_stats": {"name": "Channel stats", "fn": sync_stats, "about": "Subscribers, views and videos for the sidebar."},
    "sync_analytics": {"name": "Analytics", "fn": sync_analytics, "about": "Views, watch time, top videos and revenue for every period."},
}
locks = {job_id: threading.Lock() for job_id in JOBS}
last_runs: dict[str, dict] = {}  # latest result per job, in memory only (no run history is stored)


def all_channels() -> list[Channel]:
    with get_session() as db:
        return list(db.exec(select(Channel)))


def run_job(job_id: str):
    lock = locks[job_id]
    if not lock.acquire(blocking=False):
        log.info("%s already running, skipped", job_id)
        return
    try:
        started, status, message = now(), "ok", ""
        try:
            message = JOBS[job_id]["fn"]() or ""
        except Exception as e:
            log.warning("%s failed: %s", job_id, e)
            status, message = "error", friendly_error(e)
        last_runs[job_id] = {"started_at": started, "status": status, "message": message}
    finally:
        lock.release()


def run_now(job_id: str):
    # One-off job in the scheduler's thread pool; the lock stops it overlapping a scheduled run.
    scheduler.add_job(run_job, args=[job_id], id=f"{job_id}:manual", replace_existing=True)


def first_run(job_id: str, minutes: int) -> datetime:
    """Continue the old rhythm after a restart instead of fetching again right away."""
    column = Channel.stats_at if job_id == "sync_stats" else AnalyticsCache.fetched_at
    with get_session() as db:
        last = db.exec(select(func.max(column))).first()
    if last and last.tzinfo is None:  # SQLite hands back naive datetimes from aggregates
        last = last.replace(tzinfo=timezone.utc)
    return max(last + timedelta(minutes=minutes), now()) if last else now()


def apply_settings():
    """(Re)schedule jobs from the Settings row. Called at startup and after Settings are saved."""
    s = get_settings()
    plan = {"sync_stats": (s.stats_minutes, s.stats_enabled), "sync_analytics": (s.analytics_minutes, s.analytics_enabled)}
    for job_id, (minutes, enabled) in plan.items():
        job = scheduler.get_job(job_id)
        if job and job.trigger.interval.total_seconds() == minutes * 60:
            if enabled:
                job.resume()
            else:
                job.pause()
            continue
        scheduler.add_job(
            run_job, "interval", minutes=minutes, args=[job_id], id=job_id, replace_existing=True,
            max_instances=1, coalesce=True, misfire_grace_time=60,
            next_run_time=first_run(job_id, minutes) if enabled else None,  # None = added paused
        )


def start():
    scheduler.start()
    apply_settings()


def status() -> list[dict]:
    s = get_settings()
    minutes = {"sync_stats": s.stats_minutes, "sync_analytics": s.analytics_minutes}
    out = []
    for job_id, meta in JOBS.items():
        job = scheduler.get_job(job_id)
        out.append({
            "id": job_id,
            **meta,
            "minutes": minutes[job_id],
            "running": locks[job_id].locked(),
            "queued": scheduler.get_job(f"{job_id}:manual") is not None,
            "paused": not job or job.next_run_time is None,
            "next_run": job.next_run_time if job else None,
            "last": last_runs.get(job_id),
        })
    return out
