"""Pluggable pipeline engine.

A step is one class: a name, the queue it needs, the steps it reads from, and `run(ctx) -> dict`.
Steps self-register with @register; config/pipelines.toml decides which run and in what order.
The runner persists each step's output, so a re-run skips finished steps and resumes at the failed one.
"""

import logging
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ytstudio.domain.errors import NotFound, PermanentError
from ytstudio.domain.events import JobFailed
from ytstudio.domain.models import Asset, Channel, Queue, Run, RunStatus, Schedule, User
from ytstudio.ports import Deps, DistributedLock, StepPlan, TaskDispatcher

log = logging.getLogger(__name__)

REGISTRY: dict[str, type["Step"]] = {}


def register(cls: type["Step"]) -> type["Step"]:
    if cls.name in REGISTRY:
        raise ValueError(f"duplicate step name {cls.name}")
    REGISTRY[cls.name] = cls
    return cls


@dataclass
class Context:
    run: Run
    user: User
    channel: Channel
    schedule: Schedule
    outputs: dict[str, Any] = field(default_factory=dict)

    @property
    def inputs(self) -> dict[str, Any]:
        return self.run.inputs

    @property
    def regenerate(self) -> bool:
        return bool(self.inputs.get("regenerate"))

    def asset(self, kind: str, title: str, data: dict[str, Any], parent_id: int | None = None) -> Asset:
        """A new review-queue item owned by this run's user and channel."""
        return Asset(
            id=None,
            user_id=self.user.id,
            channel_id=self.channel.id,
            run_id=self.run.id,
            kind=kind,
            title=title,
            data=data,
            parent_id=parent_id,
        )

    def out(self, step: str, default: Any = None) -> Any:
        if step in self.outputs:
            return self.outputs[step]
        if default is not None:
            return default
        raise PermanentError(f"step output '{step}' is missing; is it before this step in the pipeline?")


class Step(ABC):
    """Template Method: `execute` checks inputs then calls `run`. Fan-out steps override items/run_item/merge."""

    name: ClassVar[str]
    queue: ClassVar[Queue] = Queue.IO
    needs: ClassVar[tuple[str, ...]] = ()
    critical: ClassVar[bool] = True  # non-critical LLM steps stop when the daily budget is reached
    fan_out: ClassVar[bool] = False

    def __init__(self, deps: Deps):
        self.deps = deps

    def execute(self, ctx: Context) -> dict[str, Any]:
        missing = [n for n in self.needs if n not in ctx.outputs]
        if missing:
            raise PermanentError(f"{self.name} needs outputs from {missing}")
        return self.run(ctx)

    def run(self, ctx: Context) -> dict[str, Any]:
        if self.fan_out:  # in-process fallback: same result the chord would produce
            return self.merge(ctx, [self.run_item(ctx, i) for i in self.items(ctx)])
        raise NotImplementedError

    def items(self, ctx: Context) -> list[Any]:
        raise NotImplementedError

    def run_item(self, ctx: Context, item: Any) -> Any:
        raise NotImplementedError

    def merge(self, ctx: Context, results: list[Any]) -> dict[str, Any]:
        raise NotImplementedError


@dataclass
class StartResult:
    run_id: int
    status: str
    message: str = ""


class PipelineRunner:
    def __init__(
        self,
        deps: Deps,
        dispatcher: TaskDispatcher,
        lock: DistributedLock,
        pipelines: dict[str, list[str]],
        lock_ttl_s: int = 4 * 3600,
    ):
        self.deps, self.dispatcher, self.lock, self.pipelines, self.lock_ttl = (
            deps,
            dispatcher,
            lock,
            pipelines,
            lock_ttl_s,
        )

    def plan(self, steps: list[str]) -> list[StepPlan]:
        unknown = [s for s in steps if s not in REGISTRY]
        if unknown:
            raise PermanentError(f"unknown pipeline steps {unknown}; is the step module imported?")
        return [StepPlan(s, REGISTRY[s].queue.value, REGISTRY[s].fan_out) for s in steps]

    @staticmethod
    def lock_key(run: Run) -> str:
        return f"{run.channel_id}:{run.pipeline}"

    def _lock(self, run: Run) -> bool:
        """The channel's lock for this pipeline. A lock still held by a run that has ended (its worker was killed, so
        nothing released it, and the run was then marked failed) is taken over instead of blocking for hours."""
        key = self.lock_key(run)
        if self.lock.acquire(key, self.lock_ttl, owner=str(run.id)):
            return True
        holder = self.lock.holder(key)
        if not holder or not holder.isdigit():
            return False
        with self.deps.uow() as uow:
            try:
                ended = uow.runs.get(int(holder)).status not in (
                    RunStatus.RUNNING,
                    RunStatus.PENDING,
                    RunStatus.WAITING,
                )
            except NotFound:
                ended = True
        if ended:
            log.warning("run %s ended without releasing %s; run %s takes it", holder, key, run.id)
        return ended and self.lock.release(key, holder) and self.lock.acquire(key, self.lock_ttl, owner=str(run.id))

    def start(
        self,
        user_id: int,
        channel_id: str,
        pipeline: str,
        idem_key: str,
        inputs: dict[str, Any],
        steps: list[str] | None = None,
    ) -> StartResult:
        steps = steps or self.pipelines[pipeline]
        plan = self.plan(steps)
        with self.deps.uow() as uow:
            uow.channels.get(channel_id, user_id)  # owner check
            run, created = uow.runs.create(user_id, channel_id, pipeline, idem_key, inputs | {"steps": steps})
            uow.commit()
        if not created and run.status in (RunStatus.DONE, RunStatus.RUNNING, RunStatus.PENDING, RunStatus.WAITING):
            return StartResult(run.id, run.status, "already ran or running for this key")
        if not self._lock(run):
            with self.deps.uow() as uow:
                uow.runs.set_status(run.id, RunStatus.SKIPPED, "another run for this channel and pipeline is active")
                uow.commit()
            return StartResult(run.id, RunStatus.SKIPPED, "another run for this channel and pipeline is active")
        with self.deps.uow() as uow:
            if not created:
                uow.runs.reset_failed(run.id)  # resume: finished steps keep their output and are skipped
            uow.runs.set_status(run.id, RunStatus.RUNNING)
            uow.commit()
        self.dispatcher.start_pipeline(run.id, plan, idem_key)
        return StartResult(run.id, RunStatus.RUNNING)

    def retry(self, run_id: int, user_id: int) -> StartResult:
        with self.deps.uow() as uow:
            run = uow.runs.get(run_id)
        if run.user_id != user_id:
            raise NotFound("run")
        return self.start(run.user_id, run.channel_id, run.pipeline, run.idem_key, run.inputs, run.inputs.get("steps"))

    def context(self, run_id: int) -> Context:
        with self.deps.uow() as uow:
            run = uow.runs.get(run_id)
            ctx = Context(
                run=run,
                user=uow.users.get(run.user_id),
                channel=uow.channels.get(run.channel_id),
                schedule=uow.schedules.get(run.channel_id),
            )
        ctx.outputs = dict(run.inputs.get("seed_outputs", {})) | run.outputs
        return ctx

    def _begin(self, run_id: int, step: str) -> Context | None:
        with self.deps.uow() as uow:
            if uow.runs.step_status(run_id, step) == RunStatus.DONE:
                return None  # idempotent re-run: output already saved
            uow.runs.start_step(run_id, step)
            uow.commit()
        return self.context(run_id)

    def _save(self, run_id: int, step: str, output: dict[str, Any]) -> dict[str, Any]:
        with self.deps.uow() as uow:
            uow.runs.finish_step(run_id, step, output)
            uow.commit()
        return output

    def run_step(self, run_id: int, step: str) -> None:
        if (ctx := self._begin(run_id, step)) is None:
            return
        self._save(run_id, step, REGISTRY[step](self.deps).execute(ctx))

    def fan_items(self, run_id: int, step: str) -> list[Any] | None:
        if (ctx := self._begin(run_id, step)) is None:
            return None
        return REGISTRY[step](self.deps).items(ctx)

    def run_item(self, run_id: int, step: str, item: Any) -> Any:
        return REGISTRY[step](self.deps).run_item(self.context(run_id), item)

    def merge(self, run_id: int, step: str, results: list[Any]) -> None:
        self._save(run_id, step, REGISTRY[step](self.deps).merge(self.context(run_id), results))

    def waiting(self, run_id: int, step: str, reason: str) -> None:
        with self.deps.uow() as uow:
            uow.runs.fail_step(run_id, step, reason, status=RunStatus.WAITING)
            uow.runs.set_status(run_id, RunStatus.WAITING, reason)
            uow.commit()

    def resumed(self, run_id: int) -> None:
        with self.deps.uow() as uow:
            uow.runs.set_status(run_id, RunStatus.RUNNING)
            uow.commit()

    def fail(self, run_id: int, step: str, error: str, task: str, payload: dict[str, Any]) -> None:
        with self.deps.uow() as uow:
            run = uow.runs.get(run_id)
            uow.runs.fail_step(run_id, step, error)
            uow.runs.set_status(run_id, RunStatus.FAILED, f"{step}: {error}")
            uow.runs.dead_letter(run_id, step, task, payload, error)
            uow.commit()
        self.lock.release(self.lock_key(run), str(run_id))
        self.deps.publish(JobFailed(user_id=run.user_id, run_id=run_id, step=step, error=error))

    def finish(self, run_id: int) -> None:
        with self.deps.uow() as uow:
            run = uow.runs.get(run_id)
            uow.runs.set_status(run_id, RunStatus.DONE)
            uow.commit()
        self.lock.release(self.lock_key(run), str(run_id))


class InlineDispatcher:
    """Runs a pipeline synchronously in-process. Used by tests and by `run now` when no worker is wanted."""

    def __init__(self) -> None:
        self.runner: PipelineRunner | None = None

    def start_pipeline(self, run_id: int, steps: list[StepPlan], idem_key: str) -> None:
        assert self.runner
        for s in steps:
            try:
                if s.fan_out:
                    items = self.runner.fan_items(run_id, s.name)
                    if items is not None:
                        self.runner.merge(run_id, s.name, [self.runner.run_item(run_id, s.name, i) for i in items])
                else:
                    self.runner.run_step(run_id, s.name)
            except Exception as e:
                self.runner.fail(run_id, s.name, str(e), "inline", {"run_id": run_id, "step": s.name})
                return
        self.runner.finish(run_id)
