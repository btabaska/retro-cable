"""The TV endpoints the browser and tvOS apps call: the dial, the guide and tuning in."""

import calendar
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from retro_cable.catalog.models import Channel
from retro_cable.scheduler import ChannelOffAir, Scheduler, Slot, simulated_now

Year = Annotated[int, Query(ge=1990, le=2010, description="Simulated year")]
MonthDay = Annotated[
    str | None,
    Query(pattern=r"^\d{2}-\d{2}$", description="MM-DD; defaults to today's date"),
]


class ChannelInfo(BaseModel):
    id: str
    name: str
    type: Literal["network", "marathon", "movie"]
    number: int
    call_sign: str
    network: str | None
    on_air: bool
    library: bool
    launch: date | None


class Lineup(BaseModel):
    year: int
    date: date
    weekday: str
    now: datetime  # simulated, local
    timezone: str
    channels: list[ChannelInfo]


class GuideSlot(BaseModel):
    kind: Literal["episode", "movie", "bumper", "filler"]
    start: datetime
    end: datetime
    seconds: int
    title: str
    show: str | None
    episode_title: str
    season: int | None
    episode: int | None
    air_date: date | None
    new: bool
    transplant: bool
    holiday: str | None = Field(
        description="Holiday id this program is tagged for while that holiday is in force"
    )
    holiday_name: str | None = Field(description="Display name of `holiday`, e.g. Halloween")


class TunedSlot(GuideSlot):
    media_url: str | None


class GuideRow(BaseModel):
    channel: ChannelInfo
    slots: list[GuideSlot]


class Guide(BaseModel):
    date: date
    weekday: str
    timezone: str
    start: datetime
    end: datetime
    channels: list[GuideRow]


class Tune(BaseModel):
    channel: ChannelInfo
    now: datetime
    offset_seconds: float
    current: TunedSlot
    next: list[TunedSlot]


def router(
    scheduler: Callable[[], Scheduler],
    clock: Callable[[], datetime],
    sign: Callable[[str], str],
) -> APIRouter:
    api = APIRouter()

    def info(s: Scheduler, channel: Channel, day: date) -> ChannelInfo:
        ident = s.identity(channel.id, day)
        return ChannelInfo(
            id=channel.id,
            name=channel.name,
            type=channel.type,
            number=ident.number,
            call_sign=ident.call_sign,
            network=ident.network,
            on_air=s.on_air(channel, day),
            library=channel.library,
            launch=channel.launch,
        )

    def sim_date(s: Scheduler, year: int, month_day: str | None) -> date:
        if month_day is None:
            return simulated_now(clock(), year, s.tz).date()
        month, day = (int(x) for x in month_day.split("-"))
        if month == 2 and day == 29 and not calendar.isleap(year):
            day = 28
        try:
            return date(year, month, day)
        except ValueError as e:
            raise HTTPException(422, f"bad date: {month_day}") from e

    def guide_slot(s: Scheduler, slot: Slot) -> dict[str, object]:
        return {
            "kind": slot.kind,
            "start": slot.start.astimezone(s.tz),
            "end": slot.end.astimezone(s.tz),
            "seconds": slot.seconds,
            "title": slot.title,
            "show": slot.show,
            "episode_title": slot.episode_title,
            "season": slot.season,
            "episode": slot.episode,
            "air_date": slot.air_date,
            "new": slot.new,
            "transplant": slot.transplant,
            "holiday": slot.holiday,
            "holiday_name": s.tagger.holidays[slot.holiday].name if slot.holiday else None,
        }

    def tuned(s: Scheduler, slot: Slot) -> TunedSlot:
        return TunedSlot.model_validate(
            {**guide_slot(s, slot), "media_url": sign(slot.path) if slot.path else None}
        )

    @api.get("/channels", name="getChannels")
    def get_channels(year: Year, date_: Annotated[MonthDay, Query(alias="date")] = None) -> Lineup:
        """Every channel with its identity on that date; hidden ones have on_air false."""
        s = scheduler()
        day = sim_date(s, year, date_)
        now = simulated_now(clock(), year, s.tz)
        if date_ is not None:
            now = datetime.combine(day, now.time(), tzinfo=s.tz)
        return Lineup(
            year=year,
            date=day,
            weekday=day.strftime("%A"),
            now=now,
            timezone=str(s.tz),
            channels=[info(s, c, day) for c in s.catalog.channels.channels],
        )

    @api.get("/guide", name="getGuide")
    def get_guide(
        year: Year,
        date_: Annotated[MonthDay, Query(alias="date")] = None,
        start: Annotated[
            str, Query(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="Local HH:MM")
        ] = "00:00",
        hours: Annotated[float, Query(gt=0, le=36)] = 24,
    ) -> Guide:
        """Back-to-back slots for every channel on the dial, from `start` for `hours`."""
        s = scheduler()
        day = sim_date(s, year, date_)
        hh, mm = (int(x) for x in start.split(":"))
        begin = datetime.combine(day, time(hh, mm), tzinfo=s.tz)
        finish = begin + timedelta(hours=hours)
        on = sorted(s.channels_on(day), key=lambda c: s.identity(c.id, day).number)
        rows = [
            GuideRow(
                channel=info(s, c, day),
                slots=[
                    GuideSlot.model_validate(guide_slot(s, x))
                    for x in s.between(c.id, begin, finish)
                ],
            )
            for c in on
        ]
        return Guide(
            date=day,
            weekday=day.strftime("%A"),
            timezone=str(s.tz),
            start=begin,
            end=finish,
            channels=rows,
        )

    @api.get("/tune/{channel_id}", name="tune", responses={404: {"description": "Not on the dial"}})
    def tune(channel_id: str, year: Year, count: Annotated[int, Query(ge=0, le=10)] = 3) -> Tune:
        """What is airing now on a channel, how far in, and what comes next."""
        s = scheduler()
        now = simulated_now(clock(), year, s.tz)
        try:
            channel = s.catalog.channel(channel_id)
            current, upcoming = s.now_playing(channel_id, now, count=count)
        except (KeyError, ChannelOffAir) as e:
            raise HTTPException(404, f"{channel_id} is not on the dial in {year}") from e
        return Tune(
            channel=info(s, channel, now.date()),
            now=now,
            offset_seconds=(now - current.start).total_seconds(),
            current=tuned(s, current),
            next=[tuned(s, x) for x in upcoming],
        )

    return api
