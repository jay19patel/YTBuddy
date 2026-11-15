"""Daily pipeline end to end with fakes, plus the engine's guarantees: idempotency, resume, locks."""

from typing import Any

import pytest

from ytstudio.application.pipeline import REGISTRY, Context, Step, register
from ytstudio.domain.errors import PermanentError
from ytstudio.domain.events import BriefReady, JobFailed
from ytstudio.domain.models import Queue, RunStatus, idem_key

FLAKY = {"fail": True}


@pytest.fixture(autouse=True, scope="module")
def test_steps():
    @register
    class Flaky(Step):
        name = "t_flaky"
        queue = Queue.IO

        def run(self, ctx: Context) -> dict[str, Any]:
            if FLAKY["fail"]:
                raise PermanentError("flaky failed")
            return {"ok": True}

    @register
    class Count(Step):
        name = "t_count"
        queue = Queue.IO
        runs = 0

        def run(self, ctx: Context) -> dict[str, Any]:
            type(self).runs += 1
            return {"n": type(self).runs}

    yield
    REGISTRY.pop("t_flaky")
    REGISTRY.pop("t_count")


def assets(container, run_id):
    with container.uow() as uow:
        return uow.assets.for_run(run_id)


def test_daily_pipeline_end_to_end(container, user_and_channel):
    user_id, ch = user_and_channel
    res = container.scheduling.run_now(user_id, ch)
    with container.uow() as uow:
        run = uow.runs.get(res.run_id)
    assert run.status == RunStatus.DONE, run
    out = run.outputs

    # performance vs own median
    perf = out["fetch_performance"]
    assert perf["count"] == 6 and perf["top"][0]["video_id"] == "own0" and perf["top"][0]["multiple"] > 4
    assert perf["retention"]["own0"]["analysis"]["available"] is True

    # trends: deduped across providers, own videos dropped, ranked by multiple vs the source channel's median
    assert {c["video_id"] for c in out["discover_trends"]["candidates"]} == {"t1", "t2"}
    outliers = out["rank_trends"]["outliers"]
    assert [o["video_id"] for o in outliers] == ["t1"]  # t2 is normal for its big channel
    assert outliers[0]["multiple"] >= 2 and outliers[0]["age_days"] == 3.0 and outliers[0]["velocity"] == 3000.0

    # ideas: invented references are removed
    ideas = out["generate_ideas"]["ideas"]
    assert [i["format"] for i in ideas] == ["long"] * 2 + ["short"] * 5 and [i["rank"] for i in ideas] == list(
        range(1, 8)
    )
    assert ideas[0]["outlier_refs"] == ["t1"] and ideas[0]["builds_on"] == ["own1"] and ideas[0]["source"] == "auto"
    assert all(i["effort"] == "short" for i in ideas[2:])

    # every idea is developed: long = script + package + SEO, short = one script with its own title and description
    developed = out["develop_ideas"]["developed"]
    assert [d["idea"]["rank"] for d in developed] == list(range(1, 8))
    long_one, short_one = developed[0], developed[2]
    sc = long_one["script"]  # 5 hooks scored by the vendored hook panel, best first, best one opens the script
    assert len(sc["hooks"]) == 5 and sc["hook"] == sc["hooks"][0]
    assert sc["hooks"][0]["panel_score"] >= sc["hooks"][-1]["panel_score"]
    assert all("score" in p["lint"] for p in long_one["package"]["pairs"]) and "seo" in long_one
    assert short_one["script"]["format"] == "short" and short_one["script"]["title"] and "package" not in short_one

    # everything lands in review; nothing is approved or published automatically
    kinds = sorted(a.kind for a in assets(container, run.id))
    assert kinds == ["brief"] + ["idea"] * 7 + ["package"] * 2 + ["script"] * 7 + ["seo"] * 2
    assert all(a.status == "needs_review" for a in assets(container, run.id))
    events = [e for _, e in container.events.notifier.sent]
    assert any(isinstance(e, BriefReady) for e in events)


def test_run_is_idempotent_per_channel_pipeline_and_day(container, user_and_channel):
    user_id, ch = user_and_channel
    first = container.scheduling.run_now(user_id, ch)
    second = container.scheduling.run_now(user_id, ch)
    assert second.run_id == first.run_id and second.status == RunStatus.DONE
    assert len([a for a in assets(container, first.run_id) if a.kind == "brief"]) == 1


def test_failed_run_resumes_from_failed_step(container, user_and_channel):
    from tests.test_pipeline import FLAKY

    user_id, ch = user_and_channel
    count = REGISTRY["t_count"]
    count.runs = 0  # type: ignore[attr-defined]
    FLAKY["fail"] = True
    res = container.runner.start(user_id, ch, "test", "k1", {}, steps=["t_count", "t_flaky"])
    with container.uow() as uow:
        assert uow.runs.get(res.run_id).status == RunStatus.FAILED
    assert any(isinstance(e, JobFailed) for _, e in container.events.notifier.sent)
    assert container.lock.held == {}  # lock released on failure

    FLAKY["fail"] = False
    retry = container.runner.retry(res.run_id, user_id)
    assert retry.run_id == res.run_id
    with container.uow() as uow:
        run = uow.runs.get(res.run_id)
    assert run.status == RunStatus.DONE
    assert count.runs == 1  # type: ignore[attr-defined]  # finished step was not run again


def test_lock_contention_skips_second_run(container, user_and_channel):
    user_id, ch = user_and_channel
    container.lock.acquire(f"{ch}:test", 60, "someone-else")
    res = container.runner.start(user_id, ch, "test", "k2", {}, steps=["t_count"])
    assert res.status == RunStatus.SKIPPED


def test_a_lock_left_by_a_run_that_ended_is_taken_over(container, user_and_channel):
    user_id, ch = user_and_channel
    with container.uow() as uow:  # its worker was killed: the run was marked failed but nothing released the lock
        dead, _ = uow.runs.create(user_id, ch, "test", "k-dead", {})
        uow.runs.set_status(dead.id, RunStatus.FAILED, "worker killed")
        alive, _ = uow.runs.create(user_id, ch, "test2", "k-alive", {})
        uow.runs.set_status(alive.id, RunStatus.RUNNING)
        uow.commit()
    container.lock.acquire(f"{ch}:test", 60, str(dead.id))
    assert container.runner.start(user_id, ch, "test", "k4", {}, steps=["t_count"]).status == RunStatus.RUNNING
    container.lock.acquire(f"{ch}:test2", 60, str(alive.id))  # a run still going keeps its lock
    assert container.runner.start(user_id, ch, "test2", "k5", {}, steps=["t_count"]).status == RunStatus.SKIPPED


def test_owner_check_on_start(container, user_and_channel):
    from ytstudio.domain.errors import NotFound

    other = container.accounts.owner("other@example.com")
    with pytest.raises(NotFound):
        container.runner.start(other.id, user_and_channel[1], "test", "k3", {}, steps=["t_count"])


def test_unknown_step_is_rejected(container, user_and_channel):
    with pytest.raises(PermanentError):
        container.runner.start(user_and_channel[0], user_and_channel[1], "test", "k4", {}, steps=["nope"])


def test_idem_key_format():
    from datetime import date

    assert idem_key("UC1", "daily", date(2026, 3, 8)) == "UC1:daily:2026-03-08"


def test_develop_and_regenerate(container, user_and_channel):
    user_id, ch = user_and_channel
    run = container.scheduling.run_now(user_id, ch)
    idea = next(a for a in assets(container, run.run_id) if a.kind == "idea" and a.data["rank"] == 2)
    dev = container.review.develop(user_id, idea.id)
    with container.uow() as uow:
        assert uow.runs.get(dev.run_id).status == RunStatus.DONE
        developed = uow.assets.for_run(dev.run_id)
    assert {a.kind for a in developed} == {"script", "package", "seo"}  # no second brief replaces today's
    assert all(a.parent_id == idea.id for a in developed)

    script = next(a for a in assets(container, run.run_id) if a.kind == "script")
    regen = container.review.regenerate(user_id, script.id)
    with container.uow() as uow:
        assert uow.runs.get(regen.run_id).status == RunStatus.DONE
        updated = uow.assets.get(script.id, user_id)
    assert len(updated.data["history"]) == 1 and updated.status == "needs_review"
    assert container.deps.llm.calls[-1].regenerate is True  # bypasses the LLM cache
