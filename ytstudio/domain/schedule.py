"""When does a channel's daily run happen next? Pure, DST-aware."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


def local_wall_to_utc(day: date, at: time, tz: ZoneInfo) -> datetime:
    """A wall-clock time on a day, in UTC. Times skipped by a DST jump move forward by the gap."""
    # fold=0: a time inside the spring-forward gap uses the pre-jump offset, which lands after the gap;
    # a repeated fall-back time resolves to its first occurrence.
    return datetime.combine(day, at, tzinfo=tz).astimezone(UTC)


def next_run(after: datetime, tz_name: str, at: time, days: str) -> datetime | None:
    """First scheduled instant strictly after `after` (UTC). `days` holds weekday digits, Monday=0."""
    if not days:
        return None
    tz = ZoneInfo(tz_name)
    start = after.astimezone(tz).date()
    for offset in range(8):
        day = start + timedelta(days=offset)
        if str(day.weekday()) not in days:
            continue
        when = local_wall_to_utc(day, at, tz)
        if when > after:
            return when
    return None


def local_day(instant: datetime, tz_name: str) -> date:
    return instant.astimezone(ZoneInfo(tz_name)).date()


def next_quota_reset(now: datetime) -> datetime:
    """YouTube Data API quota resets at midnight Pacific time."""
    tomorrow = now.astimezone(PACIFIC).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0, 0), tzinfo=PACIFIC).astimezone(UTC)


def quota_day(now: datetime) -> date:
    return now.astimezone(PACIFIC).date()
