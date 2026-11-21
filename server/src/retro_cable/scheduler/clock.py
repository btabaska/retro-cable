"""Simulated time: the chosen year, today's month and day, and the local wall-clock time."""

from calendar import isleap
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Each channel's day runs from 5 a.m. to 5 a.m., like a station's broadcast day, so the
# program running at midnight carries over naturally and the day closes at a quiet hour.
BROADCAST_DAY_START = time(5, 0)
FIRST_YEAR, LAST_YEAR = 1990, 2010  # the years Retro Cable recreates
# The year the API and the apps use for All Years: every channel and every episode, with no
# year to recreate, so simulated time is the real local time (scheduler.engine, `all_years`).
ALL_YEARS = 0


def simulated_now(real: datetime, year: int, tz: ZoneInfo) -> datetime:
    """Map a real instant to the same local date and time in `year` (Feb 29 -> Feb 28). In
    All Years it stays the real local time."""
    local = real.astimezone(tz)
    if year == ALL_YEARS:
        return local
    day = local.day
    if local.month == 2 and day == 29 and not isleap(year):
        day = 28
    return datetime.combine(date(year, local.month, day), local.time(), tzinfo=tz).replace(
        fold=local.fold
    )


def broadcast_day(moment: datetime) -> date:
    """The broadcast day an aware local datetime falls in."""
    if moment.timetz().replace(tzinfo=None) >= BROADCAST_DAY_START:
        return moment.date()
    return moment.date() - timedelta(days=1)


def broadcast_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, BROADCAST_DAY_START, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), BROADCAST_DAY_START, tzinfo=tz)
    return start, end
