"""Celery integration with a real Redis broker and an in-process worker: chain + chord, idempotent re-run,
resume, lock contention, quota reschedule, transient retry. Skipped when Redis is not running."""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis

from ytstudio.adapters.celery_dispatcher import CeleryDispatcher
from ytstudio.adapters.redis_kv import RedisLock, client
from ytstudio.application.pipeline import REGISTRY, Context, PipelineRunner, Step, register
from ytstudio.domain.errors import PermanentError, QuotaExceeded, TransientError
from ytstudio.domain.models import Queue, RunStatus

STATE: dict[str, Any] = {}


def _redis_up() -> bool:
    try:
        return bool(client("redis://localhost:6379/13").ping())
    except redis.ConnectionError:
        return False


pytestmark = pytest.mark.skipif(not _redis_up(), reason="Redis not running")


@pytest.fixture(scope="module", autouse=True)
def steps():
    @register
    class Once(Step):
        name = "c_count"
        queue = Queue.IO

        def run(self, ctx: Context) -> dict[str, Any]:
            STATE["count"] = STATE.get("count", 0) + 1
            return {"n": STATE["count"]}

    @register
    class Flaky(Step):
        name = "c_flaky"
        queue = Queue.LLM

        def run(self, ctx: Context) -> dict[str, Any]:
            mode = STATE.get("flaky")
            if mode == "permanent":
                raise PermanentError("broken input")
            if mode == "transient" and STATE.setdefault("tries", 0) < 1:
                STATE["tries"] += 1
                raise TransientError("503")
            if mode == "quota" and not STATE.get("quota_hit"):
                STATE["quota_hit"] = True
                raise QuotaExceeded(datetime.now(UTC) + timedelta(seconds=1))
            return {"ok": True}

    @register
    class Fan(Step):
        name = "c_fan"
        queue = Queue.MEDIA
        fan_out = True

        def items(self, ctx: Context) -> list[Any]:
            return [1, 2, 3]

        def run_item(self, ctx: Context, item: Any) -> Any:
            return item * 10

        def merge(self, ctx: Context, results: list[Any]) -> dict[str, Any]:
            return {"sum": sum(results)}

    yield
    for n in ("c_count", "c_flaky", "c_fan"):
        REGISTRY.pop(n)


@pytest.fixture
def celery_container(container, monkeypatch):
    from celery.contrib.testing.worker import start_worker

    from ytstudio.interfaces import worker

    for db in (13, 14, 15):
        client(f"redis://localhost:6379/{db}").flushdb()
    r = client("redis://localhost:6379/15")
    container.o.update(
        redis=r,
        lock=RedisLock(r),
        dispatcher=CeleryDispatcher(worker.celery_app, {"io": [60, 90], "llm": [60, 90], "media": [60, 90]}),
    )
    for name in ("redis", "lock", "dispatcher", "runner", "scheduling", "review", "shorts"):
        container.__dict__.pop(name, None)
    monkeypatch.setattr(worker, "backoff", lambda retries: 0.2)
    STATE.clear()
    with start_worker(
        worker.celery_app,
        pool="threads",
        concurrency=4,
        perform_ping_check=False,
        queues=["io", "llm", "media"],
        shutdown_timeout=10,
    ):
        yield container


def wait(container, run_id: int, until=("done", "failed", "skipped"), timeout: float = 40) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with container.uow() as uow:
            run = uow.runs.get(run_id)
        if run.status in until:
            return run
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} stuck in {run.status}")


def start(c, key, steps, user_channel):
    runner: PipelineRunner = c.runner
    return runner.start(user_channel[0], user_channel[1], "ctest", key, {}, steps=steps)


def test_chain_with_chord_runs_to_done(celery_container, user_and_channel):
    res = start(celery_container, "k-chain", ["c_count", "c_fan", "c_flaky"], user_and_channel)
    run = wait(celery_container, res.run_id)
    assert run.status == RunStatus.DONE
    assert run.outputs["c_fan"] == {"sum": 60} and run.outputs["c_count"] == {"n": 1}
    assert celery_container.lock.acquire(f"{user_and_channel[1]}:ctest", 5, "probe")  # lock was released


def test_daily_pipeline_through_celery(celery_container, user_and_channel):
    res = celery_container.scheduling.run_now(*user_and_channel)
    run = wait(celery_container, res.run_id, timeout=60)
    assert run.status == RunStatus.DONE, run
    assert run.outputs["save_brief"]["brief_id"]


def test_idempotent_rerun_enqueues_nothing(celery_container, user_and_channel):
    first = start(celery_container, "k-idem", ["c_count"], user_and_channel)
    wait(celery_container, first.run_id)
    again = start(celery_container, "k-idem", ["c_count"], user_and_channel)
    time.sleep(1)
    assert again.run_id == first.run_id and again.status == RunStatus.DONE and STATE["count"] == 1


def test_permanent_failure_dead_letters_then_resumes(celery_container, user_and_channel, db_url):
    from sqlmodel import select

    from ytstudio.adapters.persistence import tables as t
    from ytstudio.adapters.persistence.db import session_for

    STATE["flaky"] = "permanent"
    res = start(celery_container, "k-resume", ["c_count", "c_flaky"], user_and_channel)
    assert wait(celery_container, res.run_id).status == RunStatus.FAILED
    with session_for(db_url) as s:
        dead = list(s.exec(select(t.DeadLetter)))
    assert dead and dead[0].step == "c_flaky" and "broken input" in dead[0].error

    STATE["flaky"] = None
    celery_container.runner.retry(res.run_id, user_and_channel[0])
    assert wait(celery_container, res.run_id).status == RunStatus.DONE
    assert STATE["count"] == 1  # the finished step was skipped on resume
    with session_for(db_url) as s:
        assert s.exec(select(t.DeadLetter)).first().resolved_at is not None


def test_lock_contention(celery_container, user_and_channel):
    celery_container.lock.acquire(f"{user_and_channel[1]}:ctest", 30, "other-run")
    res = start(celery_container, "k-lock", ["c_count"], user_and_channel)
    assert res.status == RunStatus.SKIPPED and "count" not in STATE


def test_transient_error_is_retried(celery_container, user_and_channel):
    STATE["flaky"] = "transient"
    res = start(celery_container, "k-transient", ["c_flaky"], user_and_channel)
    run = wait(celery_container, res.run_id)
    assert run.status == RunStatus.DONE and STATE["tries"] == 1


def test_quota_exceeded_waits_for_reset_then_continues(celery_container, user_and_channel):
    STATE["flaky"] = "quota"
    res = start(celery_container, "k-quota", ["c_flaky", "c_count"], user_and_channel)
    run = wait(celery_container, res.run_id, until=("waiting", "done", "failed"))
    assert run.status in (RunStatus.WAITING, RunStatus.DONE)
    run = wait(celery_container, res.run_id)
    assert run.status == RunStatus.DONE and STATE["count"] == 1
