"""Celery app and thin tasks. Tasks translate errors into retries; all logic lives in use cases.

Run (see docker-compose.yml for the real commands):
    celery -A ytstudio.interfaces.worker worker -Q io -c 6
    celery -A ytstudio.interfaces.worker worker -Q llm -c 3
    celery -A ytstudio.interfaces.worker worker -Q media -c 1 --prefetch-multiplier 1
    celery -A ytstudio.interfaces.worker beat          # exactly one instance
"""

import json
import logging
import random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from celery import Celery, Task, chord, group, signature
from celery.exceptions import SoftTimeLimitExceeded
from celery.schedules import crontab
from celery.signals import beat_init, setup_logging, worker_shutting_down

from ytstudio.application.pipeline import Stopped
from ytstudio.config import get_settings
from ytstudio.container import get_container
from ytstudio.domain.errors import QuotaExceeded, TransientError
from ytstudio.domain.models import RunStatus
from ytstudio.logging_setup import run_id_var
from ytstudio.logging_setup import setup_logging as _setup

log = logging.getLogger(__name__)
s = get_settings()

celery_app = Celery("ytstudio", broker=s.celery_broker_url, backend=s.celery_result_backend)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=86400,
    task_acks_late=True,  # a task is acked only after it finished...
    task_reject_on_worker_lost=True,  # ...and goes back to the queue if the worker died mid-task
    worker_prefetch_multiplier=1,
    task_default_queue="io",
    task_track_started=True,
    timezone="UTC",
    broker_connection_retry_on_startup=True,
    # Longer than the longest media task (hard limit 3900 s), or Redis would hand it to a second worker.
    broker_transport_options={"visibility_timeout": 3 * 3600},
    result_backend_transport_options={"visibility_timeout": 3 * 3600},
    worker_send_task_events=False,
    beat_schedule={
        "tick": {"task": "beat.tick", "schedule": 300.0, "options": {"queue": "io", "expires": 290}},
        "sync-recent-uploads": {
            "task": "beat.sync_recent_uploads",
            "schedule": 1800.0,
            "options": {"queue": "io", "expires": 1700},
        },
        "daily-cleanup": {"task": "beat.cleanup", "schedule": crontab(hour=3, minute=17), "options": {"queue": "io"}},
        "daily-analytics": {
            "task": "beat.analytics_refresh",
            "schedule": crontab(hour=10, minute=7),
            "options": {"queue": "io"},
        },
        **{
            f"ping-{q}": {"task": "health.ping", "schedule": 60.0, "args": (q,), "options": {"queue": q, "expires": 55}}
            for q in ("io", "llm", "media")
        },
    },
)

MAX_TRANSIENT = {"io": 5, "llm": 2, "media": 2}  # LLM: the Router already retried and fell back


@setup_logging.connect
def _logging(**_: Any) -> None:
    _setup(s.log_level, s.log_json)


@beat_init.connect
def _first_tick(**_: Any) -> None:
    # Tick once at start: health turns green and due schedules dispatch now, not 5 minutes later.
    celery_app.send_task("beat.tick", queue="io", expires=290)


@worker_shutting_down.connect
def _shutdown(**_: Any) -> None:
    # Warm shutdown: running tasks finish; with acks_late, anything killed is redelivered.
    log.info("worker shutting down; in-flight tasks finish or are redelivered")


def backoff(retries: int) -> float:
    return min(600.0, 2**retries * 10) * random.uniform(0.75, 1.25)  # exponential, with jitter


@contextmanager
def step_errors(task: Task, run_id: int, step: str, queue: str) -> Iterator[None]:
    runner = get_container().runner
    token = run_id_var.set(str(run_id))
    try:
        with get_container().uow() as uow:
            if uow.runs.get(run_id).status == RunStatus.WAITING:
                runner.resumed(run_id)
        yield
    except QuotaExceeded as e:
        # Out of YouTube quota: wait for the Pacific-time reset instead of burning retries.
        runner.waiting(run_id, step, str(e))
        raise task.retry(eta=e.reset_at, exc=e, max_retries=task.request.retries + 1) from e
    except TransientError as e:
        if task.request.retries < MAX_TRANSIENT.get(queue, 3):
            raise task.retry(countdown=backoff(task.request.retries), exc=e) from e
        runner.fail(
            run_id,
            step,
            f"gave up after {task.request.retries} retries: {e}",
            task.name,
            {"run_id": run_id, "step": step},
        )
        raise
    except Stopped:
        log.info("run %s was stopped: %s not run", run_id, step)
        raise
    except SoftTimeLimitExceeded:
        runner.fail(run_id, step, "time limit exceeded", task.name, {"run_id": run_id, "step": step})
        raise
    except Exception as e:  # permanent: dead-letter it, stop the chain
        log.exception("step %s failed", step, extra={"step": step})
        runner.fail(run_id, step, str(e) or type(e).__name__, task.name, {"run_id": run_id, "step": step})
        raise
    finally:
        run_id_var.reset(token)


def queue_of(task: Task) -> str:
    return str((task.request.delivery_info or {}).get("routing_key") or "io")


@celery_app.task(bind=True, name="pipeline.step", max_retries=None)
def run_step(self: Task, run_id: int, step: str) -> None:
    with step_errors(self, run_id, step, queue_of(self)):
        get_container().runner.run_step(run_id, step)


@celery_app.task(bind=True, name="pipeline.fan_out", max_retries=None)
def fan_out(self: Task, run_id: int, step: str) -> Any:
    """Replace this task with a chord: one task per item, then a merge. The chain continues after it."""
    from ytstudio.application.pipeline import REGISTRY

    with step_errors(self, run_id, step, queue_of(self)):
        items = get_container().runner.fan_items(run_id, step)
    if items is None:
        return None  # finished on an earlier attempt
    q = REGISTRY[step].queue.value
    soft, hard = s.queue_limits[q]
    members = [
        signature(
            "pipeline.item",
            args=(run_id, step, i),
            app=celery_app,
            immutable=True,
            options={"queue": q, "soft_time_limit": soft, "time_limit": hard, "task_id": f"{self.request.id}:{n}"},
        )
        for n, i in enumerate(items)
    ]
    merge = signature("pipeline.merge", args=(run_id, step), app=celery_app, options={"queue": "io"})
    if not members:
        return self.replace(merge.clone(args=([], run_id, step)))
    return self.replace(chord(group(members), merge))


@celery_app.task(bind=True, name="pipeline.item", max_retries=None)
def run_item(self: Task, run_id: int, step: str, item: Any) -> Any:
    with step_errors(self, run_id, step, queue_of(self)):
        return get_container().runner.run_item(run_id, step, item)


@celery_app.task(bind=True, name="pipeline.merge", max_retries=None)
def merge(self: Task, results: list[Any], run_id: int, step: str) -> None:
    with step_errors(self, run_id, step, "io"):
        get_container().runner.merge(run_id, step, results)


@celery_app.task(name="pipeline.finish")
def finish(run_id: int) -> None:
    get_container().runner.finish(run_id)


# ----- beat tasks: thin, idempotent -----


def record_job(job_id: str, fn: Callable[[], str]) -> None:
    """Existing sync jobs: one at a time (lock), last result kept in Redis for the Settings page."""
    c = get_container()
    owner = str(random.random())
    c.redis.delete(f"yt:beat:job:{job_id}:queued")
    if not c.lock.acquire(f"job:{job_id}", 3600, owner):
        log.info("%s already running, skipped", job_id)
        return
    started, status, message = c.clock.now(), "ok", ""
    try:
        message = fn() or ""
    except Exception as e:
        from ytstudio.adapters.youtube import friendly_error

        log.warning("%s failed: %s", job_id, e)
        status, message = "error", friendly_error(e)
    finally:
        c.lock.release(f"job:{job_id}", owner)
    c.redis.set(
        f"yt:beat:job:{job_id}:last",
        json.dumps({"started_at": started.isoformat(), "status": status, "message": message}),
        ex=30 * 86400,
    )


def job_due(job_id: str, minutes: int) -> bool:
    raw = get_container().redis.get(f"yt:beat:job:{job_id}:last")
    if not raw:
        return True
    from datetime import datetime

    last = datetime.fromisoformat(json.loads(raw)["started_at"])
    return get_container().clock.now() - last >= timedelta(minutes=minutes) - timedelta(seconds=30)


@celery_app.task(name="beat.tick")
def tick() -> None:
    c = get_container()
    c.heartbeat.beat("tick", c.clock.now())
    c.scheduling.dispatch_due()
    st = c.sync.settings()
    if st.stats_enabled and job_due("sync_stats", st.stats_minutes):
        record_job("sync_stats", c.sync.sync_stats)
    if st.analytics_enabled and job_due("sync_analytics", st.analytics_minutes):
        record_job("sync_analytics", c.sync.sync_analytics)


@celery_app.task(name="jobs.run_now")
def run_job_now(job_id: str) -> None:
    c = get_container()
    record_job(job_id, {"sync_stats": c.sync.sync_stats, "sync_analytics": c.sync.sync_analytics}[job_id])


@celery_app.task(name="beat.sync_recent_uploads")
def sync_recent_uploads() -> None:
    """Every 30 min: new upload + its master file already here -> Shorts pipeline."""
    from ytstudio.domain.models import ChannelStatus

    c = get_container()
    with c.uow() as uow:
        channels = [ch for ch in uow.channels.list() if ch.status == ChannelStatus.OK and ch.user_id]
    for ch in channels:
        try:
            c.shorts.on_new_uploads(ch.id, c.sync.sync_uploads(ch.id))
        except Exception:
            log.exception("upload sync failed", extra={"channel_id": ch.id})


@celery_app.task(name="beat.analytics_refresh")
def analytics_refresh() -> None:
    from ytstudio.domain.models import ChannelStatus

    c = get_container()
    with c.uow() as uow:
        channels = [ch for ch in uow.channels.list() if ch.status == ChannelStatus.OK]
    for ch in channels:
        try:
            c.sync.refresh_video_analytics(ch.id)
        except Exception:
            log.exception("analytics refresh failed", extra={"channel_id": ch.id})


@celery_app.task(name="beat.cleanup")
def cleanup() -> None:
    from sqlmodel import col, delete

    from ytstudio.adapters.persistence import tables as t
    from ytstudio.adapters.persistence.db import session_for

    now = get_container().clock.now()
    with session_for(s.database_url) as db:
        db.exec(delete(t.WebSession).where(col(t.WebSession.expires_at) < now))
        db.exec(delete(t.OAuthState).where(col(t.OAuthState.expires_at) < now))
        db.exec(delete(t.DeadLetter).where(col(t.DeadLetter.resolved_at) < now - timedelta(days=30)))
        db.commit()


@celery_app.task(name="health.ping")
def ping(queue: str) -> None:
    c = get_container()
    c.heartbeat.beat(f"worker:{queue}", c.clock.now())


@celery_app.task(name="channels.detect_profile", bind=True, max_retries=3)
def detect_profile(self: Task, channel_id: str) -> None:
    c = get_container()
    try:
        c.sync.sync_uploads(channel_id)
        c.profiles.detect(channel_id)
    except TransientError as e:
        raise self.retry(countdown=backoff(self.request.retries), exc=e) from e
