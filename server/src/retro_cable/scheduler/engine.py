"""Build a channel's broadcast day from the catalog and the library.

A day runs from 5 a.m. to 5 a.m. local (see clock.py). Starting at 5 a.m., the engine asks
which day-part is active, picks a program from that part's weighted pool, and places it right
after the previous one, with one bumper in between. A part starting at 8:00 therefore waits for
the program already running, so primetime may begin at 8:04. Near 5 a.m. only programs that
fit are picked, and whatever time is left becomes filler ("Paid Programming"), so every day
closes exactly at 5 a.m. and the next one starts clean. That keeps each day a pure function of
(channel, date, catalog, seed) with no dependence on the day before.

All instants are UTC internally, so arithmetic is real elapsed time across DST changes; local
time is used only to find the active day-part and the simulated date.
"""

import hashlib
from collections import Counter, OrderedDict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from random import Random
from typing import Literal
from zoneinfo import ZoneInfo

from retro_cable.catalog import Catalog, effective_dates
from retro_cable.catalog.models import Carriage, Channel, DayPart, Identity, Template
from retro_cable.scheduler.clock import broadcast_day, broadcast_day_bounds
from retro_cable.scheduler.library import Bumper, Library, Program

SlotKind = Literal["episode", "movie", "bumper", "filler"]

EPOCH = date(1990, 1, 1)
RERUN_STRIDE = 3  # how far a show's rerun position advances per day
CACHE_DAYS = 1024
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
PAID_PROGRAMMING = "Paid Programming"
OFF_AIR = "Off Air"


class ChannelOffAir(Exception):
    """The channel had not launched by that date (and is not a library channel)."""


@dataclass(frozen=True)
class Slot:
    start: datetime  # UTC
    end: datetime  # UTC
    kind: SlotKind
    title: str
    show: str | None = None
    episode_title: str = ""
    season: int | None = None
    episode: int | None = None
    air_date: date | None = None  # effective first US air date
    new: bool = False
    transplant: bool = False
    path: str | None = None  # relative to the media root; None for filler

    @property
    def seconds(self) -> int:
        return int((self.end - self.start).total_seconds())


@dataclass(frozen=True)
class _Source:
    show: str
    kind: Literal["show", "movie"]
    weight: float


def _carried(c: Carriage, day: date) -> bool:
    return c.from_ <= day and (c.until is None or day <= c.until)


def _clock(hhmm: str) -> time:
    hh, mm = hhmm.split(":")
    return time(int(hh), int(mm))


def _stable_int(*parts: object) -> int:
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


class Scheduler:
    """Schedules for one (catalog, library, seed). Days are memoized; nothing is stored."""

    def __init__(self, catalog: Catalog, library: Library, seed: int = 0) -> None:
        self.catalog = catalog
        self.library = library
        self.seed = seed
        self.tz = ZoneInfo(catalog.channels.timezone)
        self._excluded = {o.show for o in catalog.overrides.shows if o.exclude}
        self._library_shows = {s.show for s in catalog.synthetic.synthetic if s.library}
        self._transplants = {
            s.show: s.mark for s in catalog.synthetic.synthetic if s.premiere or s.library
        }
        self._carriage: dict[str, list[Carriage]] = {}
        for c in catalog.carriage.carriage:
            self._carriage.setdefault(c.channel, []).append(c)
        self._programs = {
            show: self._effective(show, eps) for show, eps in library.episodes.items()
        }
        self._cache: OrderedDict[tuple[str, date], tuple[Slot, ...]] = OrderedDict()

    # --- catalog lookups ------------------------------------------------------------------

    def _effective(self, show: str, eps: tuple[Program, ...]) -> tuple[Program, ...]:
        dates = effective_dates(
            self.catalog,
            show,
            [(p.season or 0, p.episode or 0, p.air_date) for p in eps],
            first_year=self.library.show_years.get(show),
        )
        return tuple(replace(p, air_date=d) for p, d in zip(eps, dates, strict=True))

    def programs(self, show: str) -> tuple[Program, ...]:
        """A show's on-disk episodes in air order, with effective air dates."""
        return self._programs.get(show, ())

    def on_air(self, channel: Channel, day: date) -> bool:
        return channel.library or channel.launch is None or channel.launch <= day

    def channels_on(self, day: date) -> list[Channel]:
        return [c for c in self.catalog.channels.channels if self.on_air(c, day)]

    def identity(self, channel_id: str, day: date) -> Identity:
        channel = self.catalog.channel(channel_id)
        current = channel.identity[0]
        for ident in sorted(channel.identity, key=lambda i: i.from_):
            if ident.from_ <= day:
                current = ident
        return current

    def _template(self, channel_id: str, day: date) -> Template | None:
        for t in self.catalog.templates.templates:
            if t.channel == channel_id and t.from_ <= day and (t.until is None or day <= t.until):
                return t
        return None

    def _active_part(self, channel_id: str, local: datetime) -> tuple[DayPart | None, datetime]:
        """The day-part running at a local time (None = the default all-day part) and the UTC
        instant the next part could start."""
        day = local.date()
        midnight = datetime.combine(day + timedelta(days=1), time(0), tzinfo=self.tz)
        template = self._template(channel_id, day)
        parts = []
        if template:
            parts = sorted(
                (p for p in template.parts if p.weekday == WEEKDAYS[day.weekday()]),
                key=lambda p: p.start,
            )
        if not parts:
            return None, midnight.astimezone(UTC)
        now = local.time().replace(tzinfo=None)
        current = parts[0]
        boundary = midnight
        for p in parts:
            if _clock(p.start) <= now:
                current = p
            else:
                boundary = datetime.combine(day, _clock(p.start), tzinfo=self.tz)
                break
        return current, boundary.astimezone(UTC)

    def _pool(self, channel_id: str, part: DayPart | None, day: date) -> list[_Source]:
        active = [c for c in self._carriage.get(channel_id, []) if _carried(c, day)]
        weights: dict[tuple[str, str], float] = {}

        def add(c: Carriage, weight: float) -> None:
            key = (c.show, c.kind)
            weights[key] = weights.get(key, 0.0) + weight

        if part is None:
            for c in active:
                if c.block is None:
                    add(c, c.weight)
        else:
            base = [c for c in active if c.block == part.block] if part.block else active
            if part.block and not part.pool:
                for c in base:
                    add(c, c.weight)
            for entry in part.pool:
                if entry.show:
                    match = next((c for c in base if c.show == entry.show), None)
                    if match:
                        add(match, entry.weight)
                else:
                    for c in base:
                        if entry.tag in c.tags:
                            add(c, entry.weight * c.weight)
        return [
            _Source(show, kind, w)  # type: ignore[arg-type]
            for (show, kind), w in weights.items()
            if show not in self._excluded
        ]

    def _eligible(self, channel: Channel, source: _Source, day: date) -> list[Program]:
        if source.kind == "movie":
            movie = self.library.movies.get(source.show)
            if movie and (movie.air_date is None or movie.air_date <= day):
                return [movie]
            return []
        programs = self.programs(source.show)
        if channel.library or source.show in self._library_shows:
            return list(programs)
        return [p for p in programs if p.air_date is not None and p.air_date <= day]

    # --- building a day -------------------------------------------------------------------

    def day(self, channel_id: str, day: date) -> tuple[Slot, ...]:
        """The broadcast day starting at 5 a.m. local on `day`."""
        key = (channel_id, day)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        slots = self._build(self.catalog.channel(channel_id), day)
        self._cache[key] = slots
        if len(self._cache) > CACHE_DAYS:
            self._cache.popitem(last=False)
        return slots

    def _build(self, channel: Channel, day: date) -> tuple[Slot, ...]:
        if not self.on_air(channel, day):
            raise ChannelOffAir(f"{channel.id} is not on the air on {day}")
        start, end = (b.astimezone(UTC) for b in broadcast_day_bounds(day, self.tz))
        slots: list[Slot] = []
        aired: Counter[str] = Counter()
        premiered: set[tuple[str, int | None, int | None]] = set()
        t = start
        while t < end:
            local = t.astimezone(self.tz)
            sim_date = local.date()
            part, boundary = self._active_part(channel.id, local)
            rng = Random(f"{self.seed}:{channel.id}:{t.isoformat()}")
            candidates = [
                (s, progs)
                for s in self._pool(channel.id, part, sim_date)
                if (progs := self._eligible(channel, s, sim_date))
            ]
            if not candidates:
                t = self._fill(slots, t, min(boundary, end), OFF_AIR)
                continue
            bumper = self._bumper(channel, rng) if slots and slots[-1].kind != "filler" else None
            room = end - t
            pick = self._pick(channel, part, day, sim_date, candidates, room, rng, aired, slots)
            if pick and bumper:
                gap = timedelta(seconds=min(bumper.seconds, channel.bumper_seconds))
                if pick[0].seconds + gap.total_seconds() <= room.total_seconds():
                    slots.append(
                        Slot(
                            start=t,
                            end=t + gap,
                            kind="bumper",
                            title=channel.name,
                            path=bumper.path,
                        )
                    )
                    t += gap
            if pick is None:
                t = self._fill(slots, t, end, PAID_PROGRAMMING)
                continue
            program, new = pick
            if new:
                premiered.add((program.show, program.season, program.episode))
            aired[program.show] += 1
            slot_end = t + timedelta(seconds=program.seconds)
            slots.append(
                Slot(
                    start=t,
                    end=slot_end,
                    kind=program.kind,
                    title=program.title,
                    show=program.show,
                    episode_title=program.episode_title,
                    season=program.season,
                    episode=program.episode,
                    air_date=program.air_date,
                    new=new,
                    transplant=self._is_transplant(channel, program.show),
                    path=program.path,
                )
            )
            t = slot_end
        return tuple(slots)

    def _fill(self, slots: list[Slot], start: datetime, end: datetime, title: str) -> datetime:
        if slots and slots[-1].kind == "filler" and slots[-1].title == title:
            slots[-1] = replace(slots[-1], end=end)
        else:
            slots.append(Slot(start=start, end=end, kind="filler", title=title))
        return end

    def _bumper(self, channel: Channel, rng: Random) -> Bumper | None:
        options = self.library.bumpers.get(channel.id, ())
        if not options or channel.bumper_seconds == 0:
            return None
        return options[rng.randrange(len(options))]

    def _is_transplant(self, channel: Channel, show: str) -> bool:
        if show not in self._transplants:
            return False
        mark = self._transplants[show]
        return channel.transplant_mark if mark is None else mark

    def _pick(
        self,
        channel: Channel,
        part: DayPart | None,
        day: date,
        sim_date: date,
        candidates: list[tuple[_Source, list[Program]]],
        room: timedelta,
        rng: Random,
        aired: Counter[str],
        slots: list[Slot],
    ) -> tuple[Program, bool] | None:
        """Choose the next program that fits in `room`, and whether it is a NEW premiere."""
        limit = room.total_seconds()
        fitting = [(s, [p for p in progs if p.seconds <= limit]) for s, progs in candidates]
        fitting = [(s, progs) for s, progs in fitting if progs]
        if not fitting:
            return None

        if part is not None and part.primetime:
            done = {(s.show, s.season, s.episode) for s in slots if s.new}
            for _, progs in fitting:
                for p in progs:
                    if p.air_date == sim_date and (p.show, p.season, p.episode) not in done:
                        return p, True

        if channel.type == "movie" and channel.playlist == "ordered":
            movies = [progs[0] for _, progs in fitting]
            per_day = max(1, 86400 // max(1, sum(m.seconds for m in movies) // len(movies)))
            index = ((day - EPOCH).days * per_day + sum(aired.values())) % len(movies)
            return movies[index], False

        previous = next((s.show for s in reversed(slots) if s.show), None)
        weights = [
            s.weight * (0.25 if s.show == previous and len(fitting) > 1 else 1.0)
            for s, _ in fitting
        ]
        source, progs = rng.choices(fitting, weights=weights)[0]
        if source.kind == "movie":
            return progs[0], False
        offset = _stable_int(self.seed, channel.id, source.show) % len(progs)
        index = (offset + (day - EPOCH).days * RERUN_STRIDE + aired[source.show]) % len(progs)
        return progs[index], False

    # --- queries --------------------------------------------------------------------------

    def between(self, channel_id: str, start: datetime, end: datetime) -> list[Slot]:
        """Slots overlapping [start, end), across as many broadcast days as needed."""
        channel = self.catalog.channel(channel_id)
        first = broadcast_day(start.astimezone(self.tz))
        last = broadcast_day((end - timedelta(microseconds=1)).astimezone(self.tz))
        found: list[Slot] = []
        day = first
        while day <= last:
            if self.on_air(channel, day):
                found.extend(
                    s for s in self.day(channel_id, day) if s.end > start and s.start < end
                )
            day += timedelta(days=1)
        return found

    def now_playing(self, channel_id: str, at: datetime, count: int = 3) -> tuple[Slot, list[Slot]]:
        """The slot airing at `at` and the `count` slots after it."""
        day = broadcast_day(at.astimezone(self.tz))
        slots = list(self.day(channel_id, day))
        index = next(i for i, s in enumerate(slots) if s.start <= at < s.end)
        while len(slots) - index - 1 < count:
            day += timedelta(days=1)
            slots.extend(self.day(channel_id, day))
        return slots[index], slots[index + 1 : index + 1 + count]
