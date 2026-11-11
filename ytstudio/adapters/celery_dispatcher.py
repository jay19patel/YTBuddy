"""TaskDispatcher over Celery. The application layer never imports Celery; swapping to Prefect or Dramatiq
means a new class with the same `start_pipeline`."""

from typing import Any

from celery import Celery, chain, signature

from ytstudio.ports import StepPlan


class CeleryDispatcher:
    def __init__(self, app: Celery, queue_limits: dict[str, list[int]]):
        self.app, self.limits = app, queue_limits

    def sig(self, name: str, args: tuple[Any, ...], queue: str, task_id: str) -> Any:
        soft, hard = self.limits[queue]
        return signature(
            name,
            args=args,
            app=self.app,
            immutable=True,
            options={"queue": queue, "task_id": task_id, "soft_time_limit": soft, "time_limit": hard},
        )

    def start_pipeline(self, run_id: int, steps: list[StepPlan], idem_key: str) -> None:
        """A run is a chain of step tasks; a fan-out step replaces itself with a chord when it runs."""
        sigs = [
            self.sig(
                "pipeline.fan_out" if s.fan_out else "pipeline.step", (run_id, s.name), s.queue, f"{idem_key}:{s.name}"
            )
            for s in steps
        ]
        sigs.append(self.sig("pipeline.finish", (run_id,), "io", f"{idem_key}:finish"))
        chain(*sigs).apply_async()
