"""When does a channel's daily run happen next? Pure, DST-aware."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ytstudio.domain.errors import PermanentError

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


def parse_local_datetime(
    value: str, tz_name: str, now: datetime, min_lead: timedelta = timedelta(minutes=10)
) -> datetime:
    """A <input type="datetime-local"> value ("2026-11-20T18:30") in the channel's time zone, as UTC.
    YouTube only schedules a video for a time in the future, so a time sooner than min_lead from now is refused."""
    try:
        local = datetime.fromisoformat(value.strip())
    except ValueError as e:
        raise PermanentError("That schedule time is not valid. Pick a date and time.") from e
    when = local_wall_to_utc(local.date(), local.time(), ZoneInfo(tz_name))
    if when < now + min_lead:
        raise PermanentError(f"Pick a schedule time at least {int(min_lead.total_seconds() // 60)} minutes from now.")
    return when
