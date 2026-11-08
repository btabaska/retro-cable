"""Viewing signals for the station manager (M5), computed in code from the watch sessions.

Deterministic: the same sessions in any order give the same signals. Sessions shorter than
`MIN_SECONDS` are channel surfing and count for nothing. Real time is read in the catalog's
zone (America/New_York), so the heatmap says when Brandon actually watches.
"""

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

MIN_SECONDS = 10  # shorter is flipping past a channel
EARLY_SECONDS = 180  # a tune-away under 3 minutes is an early tune-away (a skip)
MIN_TOTAL_SECONDS = 2 * 3600  # under 2 hours in the window: not enough viewing to act on
WIDEN_SHARE = 0.5  # a neighbouring hour this busy joins the power hour


@dataclass(frozen=True)
class Session:
    channel: str
    item: str
    kind: str
    sim_date: date
    started_at: datetime  # UTC
    seconds: int
    offset: int
    runtime: int
    ended: str | None


def load_sessions(db: sqlite3.Connection, since: datetime, until: datetime) -> list[Session]:
    """Sessions that began in [since, until), oldest first."""
    rows = db.execute(
        "SELECT channel, item, kind, sim_date, started_at, seconds, offset_seconds,"
        " runtime_seconds, ended FROM watch_sessions"
        " WHERE started_at >= ? AND started_at < ? ORDER BY started_at, id",
        (since.astimezone(UTC).isoformat(), until.astimezone(UTC).isoformat()),
    )
    return [
        Session(
            channel=r["channel"],
            item=r["item"],
            kind=r["kind"],
            sim_date=date.fromisoformat(r["sim_date"]),
            started_at=datetime.fromisoformat(r["started_at"]),
            seconds=r["seconds"],
            offset=r["offset_seconds"],
            runtime=r["runtime_seconds"],
            ended=r["ended"],
        )
        for r in rows
    ]


class ShowSignal(BaseModel):
    channel: str
    show: str
    seconds: int
    sessions: int
    completion: float = Field(description="Seconds watched over seconds available (0-1)")
    early_tune_aways: int = Field(description="Tune-aways in under 3 minutes")


class ChannelSignal(BaseModel):
    channel: str
    seconds: int
    sessions: int
    mean_seconds: int = Field(description="Dwell: average seconds per program watched")


class Signals(BaseModel):
    total_seconds: int
    sessions: int
    year: int | None = Field(description="The simulated year watched most")
    shows: list[ShowSignal] = Field(description="Per channel and show, most watched first")
    channels: list[ChannelSignal] = Field(description="Most watched first")
    heatmap: list[list[int]] = Field(
        description="Seconds watched by real weekday (Monday first) and hour, in the catalog zone"
    )
    busiest_hours: list[int] = Field(description="Hours of the day, busiest first (up to 3)")


def _spread(start: datetime, seconds: int, tz: ZoneInfo, heat: list[list[int]]) -> None:
    """Add a session's seconds to the hour-of-week cells it covers."""
    t, left = start.astimezone(UTC), seconds
    while left > 0:
        local = t.astimezone(tz)
        into = local.minute * 60 + local.second
        chunk = min(left, 3600 - into)
        heat[local.weekday()][local.hour] += chunk
        left -= chunk
        t += timedelta(seconds=chunk)


def compute(sessions: list[Session], tz: ZoneInfo) -> Signals:
    kept = sorted(
        (s for s in sessions if s.seconds >= MIN_SECONDS),
        key=lambda s: (s.started_at, s.channel, s.item, s.seconds),
    )
    heat = [[0] * 24 for _ in range(7)]
    per_show: dict[tuple[str, str], list[Session]] = defaultdict(list)
    years: Counter[int] = Counter()
    for s in kept:
        per_show[(s.channel, s.item)].append(s)
        years[s.sim_date.year] += s.seconds
        _spread(s.started_at, s.seconds, tz, heat)
    shows = []
    for (channel, show), group in per_show.items():
        watched = sum(s.seconds for s in group)
        available = sum(max(1, s.runtime - s.offset) for s in group)
        shows.append(
            ShowSignal(
                channel=channel,
                show=show,
                seconds=watched,
                sessions=len(group),
                completion=round(min(1.0, watched / available), 3),
                early_tune_aways=sum(
                    1 for s in group if s.ended == "tune_away" and s.seconds < EARLY_SECONDS
                ),
            )
        )
    shows.sort(key=lambda s: (-s.seconds, s.channel, s.show))
    by_channel: dict[str, list[ShowSignal]] = defaultdict(list)
    for sig in shows:
        by_channel[sig.channel].append(sig)
    channels = [
        ChannelSignal(
            channel=c,
            seconds=(total := sum(x.seconds for x in group)),
            sessions=(n := sum(x.sessions for x in group)),
            mean_seconds=round(total / n),
        )
        for c, group in by_channel.items()
    ]
    channels.sort(key=lambda c: (-c.seconds, c.channel))
    hours = [sum(day[h] for day in heat) for h in range(24)]
    busiest = sorted((h for h in range(24) if hours[h]), key=lambda h: (-hours[h], h))[:3]
    year = max(years, key=lambda y: (years[y], y)) if years else None
    return Signals(
        total_seconds=sum(s.seconds for s in kept),
        sessions=len(kept),
        year=year,
        shows=shows,
        channels=channels,
        heatmap=heat,
        busiest_hours=busiest,
    )


def power_window(signals: Signals) -> tuple[str, int] | None:
    """The power hour: the busiest hour of the day, widened to two hours when a
    neighbouring hour has at least half as much viewing. Every day, since the scheduler's
    weekdays are the simulated ones (Brandon's Tuesday is a different weekday in 1995)."""
    if not signals.busiest_hours:
        return None
    hours = [sum(day[h] for day in signals.heatmap) for h in range(24)]
    top = signals.busiest_hours[0]
    after = hours[top + 1] if top < 23 else 0
    before = hours[top - 1] if top > 0 else 0
    if max(after, before) >= WIDEN_SHARE * hours[top]:
        start = top if after >= before else top - 1
        return f"{start:02d}:00", 2
    return f"{top:02d}:00", 1
