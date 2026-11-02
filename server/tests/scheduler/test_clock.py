from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from retro_cable.scheduler import broadcast_day, simulated_now

NY = ZoneInfo("America/New_York")


def test_keeps_month_day_and_local_wall_clock() -> None:
    real = datetime(2026, 10, 31, 0, 14, tzinfo=UTC)  # 8:14 PM Oct 30 in New York
    sim = simulated_now(real, 1998, NY)
    assert (sim.year, sim.month, sim.day, sim.hour, sim.minute) == (1998, 10, 30, 20, 14)
    assert sim.tzinfo == NY


def test_feb_29_falls_back_to_feb_28() -> None:
    real = datetime(2028, 2, 29, 17, 0, tzinfo=UTC)
    assert simulated_now(real, 1997, NY).date() == date(1997, 2, 28)
    assert simulated_now(real, 1996, NY).date() == date(1996, 2, 29)


def test_weekday_comes_from_the_simulated_year() -> None:
    real = datetime(2026, 10, 31, 16, 0, tzinfo=UTC)
    assert simulated_now(real, 1998, NY).strftime("%A") == "Saturday"
    assert simulated_now(real, 1997, NY).strftime("%A") == "Friday"


def test_broadcast_day_starts_at_five_am() -> None:
    assert broadcast_day(datetime(1998, 10, 31, 4, 59, tzinfo=NY)) == date(1998, 10, 30)
    assert broadcast_day(datetime(1998, 10, 31, 5, 0, tzinfo=NY)) == date(1998, 10, 31)
