"""POST /watch: what was watched, for the weekly station manager (M5).

The web app reports each program it plays on a channel: once it has played for a few
seconds, on a heartbeat, and when it ends (tune-away, next program, page closed). A report
with the same `session` updates its row. The server stamps the real start time and works
out the simulated date itself, and caps the seconds by the program's runtime and by the
real time that has passed, so a bad client can't inflate a show.

Rows hold no IP addresses, tokens, URLs or file paths, and nothing here logs a request.
LAN-only like the rest of the API.
"""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Protocol
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from retro_cable.db import transaction
from retro_cable.scheduler import Scheduler, simulated_now
from retro_cable.tv import check_year

MAX_SECONDS = 6 * 3600  # longer than any program on the dial
SLACK = 60  # seconds a report may run ahead of the real time since it started

EndReason = Literal["tune_away", "power_off", "program_end"]
Id = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=120)]


class WatchReport(BaseModel):
    """One program watched on one channel, so far."""

    model_config = ConfigDict(extra="forbid")

    session: str = Field(
        pattern=r"^[A-Za-z0-9_-]{8,64}$", description="Random id the app picks per program"
    )
    device: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$", description="App-chosen label, if any"
    )
    channel: Id
    item: Id = Field(description="Show or movie id")
    kind: Literal["episode", "movie"]
    season: int | None = Field(default=None, ge=0, le=999)
    episode: int | None = Field(default=None, ge=0, le=9999)
    year: Annotated[int, AfterValidator(check_year)] = Field(
        description="Simulated year 1990-2010, or 0 for All Years"
    )
    offset: int = Field(default=0, ge=0, le=MAX_SECONDS, description="Seconds in when tuned in")
    seconds: int = Field(ge=0, le=MAX_SECONDS, description="Seconds watched so far")
    runtime: int = Field(ge=1, le=MAX_SECONDS, description="The program's full length")
    ended: EndReason | None = Field(default=None, description="Why it ended; null while playing")


class SessionMoved(Exception):
    """A session id already names another channel or program."""


def record(db: sqlite3.Connection, r: WatchReport, now: datetime, tz: ZoneInfo) -> None:
    """Insert or update the report's session. Seconds only grow, and `ended` is set once."""
    now = now.astimezone(UTC)  # stored as UTC text, so the station manager can range over it
    with transaction(db):
        row = db.execute(
            "SELECT channel, item, season, episode, started_at, seconds, ended"
            " FROM watch_sessions WHERE session = ?",
            (r.session,),
        ).fetchone()
        if row is None:
            seconds = min(r.seconds, r.runtime)
            started = now - timedelta(seconds=seconds)
            db.execute(
                "INSERT INTO watch_sessions (session, device, channel, item, kind, season,"
                " episode, sim_date, started_at, updated_at, offset_seconds, seconds,"
                " runtime_seconds, ended) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.session, r.device, r.channel, r.item, r.kind, r.season, r.episode,
                    simulated_now(started, r.year, tz).date().isoformat(), started.isoformat(),
                    now.isoformat(), min(r.offset, r.runtime), seconds, r.runtime, r.ended,
                ),
            )  # fmt: skip
            return
        if (row["channel"], row["item"], row["season"], row["episode"]) != (
            r.channel,
            r.item,
            r.season,
            r.episode,
        ):
            raise SessionMoved
        elapsed = (now - datetime.fromisoformat(row["started_at"])).total_seconds()
        seconds = max(row["seconds"], min(r.seconds, r.runtime, int(elapsed) + SLACK))
        db.execute(
            "UPDATE watch_sessions SET seconds = ?, ended = ?, updated_at = ? WHERE session = ?",
            (seconds, row["ended"] or r.ended, now.isoformat(), r.session),
        )


class Sessions(Protocol):
    """What the router needs from the app's schedule cache (app.py)."""

    def __call__(self) -> Scheduler: ...

    def write(self, fn: Callable[[sqlite3.Connection], None]) -> None:
        """Run `fn` on the connection the cache watches, so it doesn't invalidate it."""
        ...


def router(schedules: Sessions, clock: Callable[[], datetime]) -> APIRouter:
    api = APIRouter()

    @api.post(
        "/watch",
        name="postWatch",
        status_code=204,
        response_class=Response,
        responses={
            409: {"description": "The session id already names another program"},
            422: {"description": "Not a valid report, or an unknown channel"},
        },
    )
    def post_watch(report: WatchReport) -> Response:
        """Record (or update) what is being watched. Fire and forget: the app ignores the
        answer, and nothing waits on it."""
        s = schedules()
        if not any(c.id == report.channel for c in s.catalog.channels.channels):
            raise HTTPException(422, "unknown channel")
        try:
            schedules.write(lambda db: record(db, report, clock(), s.tz))
        except SessionMoved as e:
            raise HTTPException(409, "session already names another program") from e
        return Response(status_code=204)

    return api
