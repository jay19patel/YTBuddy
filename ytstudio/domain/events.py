"""Domain events. Use cases emit them; the notification handler turns them into messages."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    user_id: int

    @property
    def title(self) -> str:
        return type(self).__name__

    @property
    def body(self) -> str:
        return ""

    @property
    def link(self) -> str:
        return "/"


@dataclass(frozen=True)
class BriefReady(Event):
    channel_title: str
    run_id: int

    @property
    def title(self) -> str:
        return f"Daily brief ready for {self.channel_title}"

    @property
    def body(self) -> str:
        return "Ideas, script, package and SEO are waiting in Today. Everything needs your review."

    @property
    def link(self) -> str:
        return "/today"


@dataclass(frozen=True)
class ShortsReady(Event):
    channel_title: str
    count: int

    @property
    def title(self) -> str:
        return f"{self.count} Shorts ready to review for {self.channel_title}"

    @property
    def link(self) -> str:
        return "/shorts"


@dataclass(frozen=True)
class JobFailed(Event):
    run_id: int
    step: str
    error: str

    @property
    def title(self) -> str:
        return f"Job failed at step {self.step}"

    @property
    def body(self) -> str:
        return self.error[:500]

    @property
    def link(self) -> str:
        return "/jobs"


@dataclass(frozen=True)
class ChannelNeedsReconnect(Event):
    channel_title: str

    @property
    def title(self) -> str:
        return f"Reconnect {self.channel_title}"

    @property
    def body(self) -> str:
        return "Google access expired or was revoked. Open YTStudio and reconnect the channel."

    @property
    def link(self) -> str:
        return "/channels"


@dataclass(frozen=True)
class BudgetReached(Event):
    spent_usd: float

    @property
    def title(self) -> str:
        return "Daily LLM budget reached"

    @property
    def body(self) -> str:
        return f"Spent ${self.spent_usd:.2f} today. Non-critical steps are paused until tomorrow."

    @property
    def link(self) -> str:
        return "/jobs"


@dataclass(frozen=True)
class BeatStale(Event):
    minutes: int

    @property
    def title(self) -> str:
        return "Scheduler stopped ticking"

    @property
    def body(self) -> str:
        return f"Celery Beat has not ticked for {self.minutes} minutes. Daily runs will not start."
