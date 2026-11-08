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
from retro_cable.catalog.holidays import STRENGTH_BOOST, Tagger, holiday_date, mode_for
from retro_cable.catalog.models import (
    Carriage,
    Channel,
    DayPart,
    Identity,
    PowerHour,
    Template,
)
from retro_cable.scheduler.clock import broadcast_day, broadcast_day_bounds
from retro_cable.scheduler.library import Bumper, Library, Program

SlotKind = Literal["episode", "movie", "bumper", "filler"]

EPOCH = date(1990, 1, 1)
RERUN_STRIDE = 3  # how far a show's rerun position advances per day
CACHE_DAYS = 1024
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
PAID_PROGRAMMING = "Paid Programming"
OFF_AIR = "Off Air"
# Prime time for channels whose template has no primetime part that weekday (or no template):
# where holiday movies and specials are injected.
DEFAULT_PRIME = (time(19, 0), time(23, 0))

ProgramKey = tuple[str, int | None, int | None]


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
    holiday: str | None = None  # id of the in-season holiday this program is tagged for

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


def _key(p: Program) -> ProgramKey:
    return (p.show, p.season, p.episode)


@dataclass(frozen=True)
class _Holidays:
    """What the holiday calendar asks of one channel at one moment."""

    prefer: frozenset[str]  # holidays in window with mode "on": prefer tagged episodes
    inject: frozenset[str]  # holidays in window with mode "on" or "specials_only"
    boost: float  # weight multiplier for a show with preferred episodes left
    prime: bool  # inside prime time, where specials are injected


class Scheduler:
    """Schedules for one (catalog, library, seed). Days are memoized; nothing is stored."""

    def __init__(self, catalog: Catalog, library: Library, seed: int = 0) -> None:
        self.catalog = catalog
        self.library = library
        self.seed = seed
        self.tz = ZoneInfo(catalog.channels.timezone)
        self._excluded = {o.show for o in catalog.overrides.shows if o.exclude}
        # Synthetic premieres by (channel, show); the last entry wins (admin over YAML).
        self._premieres = {(s.channel, s.show): s for s in catalog.synthetic.synthetic}
        self._carriage: dict[str, list[Carriage]] = {}
        self._power = {p.channel: p for p in catalog.power_hours}
        for c in catalog.carriage.carriage:
            self._carriage.setdefault(c.channel, []).append(c)
        self._programs = {
            show: self._effective(show, eps, None) for show, eps in library.episodes.items()
        }
        # A show with a synthetic premiere on a channel has its own dates there, and only there.
        self._channel_programs = {
            (channel, show): self._effective(show, library.episodes[show], channel)
            for channel, show in self._premieres
            if show in library.episodes
        }
        self.tagger = Tagger(catalog)
        self._tags: dict[ProgramKey, frozenset[str]] = {}
        self._seasonal: set[ProgramKey] = set()
        everything = [p for eps in self._programs.values() for p in eps]
        for p in [*everything, *library.movies.values()]:
            tags = self.tagger.tags(p.show, p.kind, p.title, p.episode_title, p.season, p.episode)
            if tags:
                self._tags[_key(p)] = tags
            if self.tagger.is_seasonal(p.show, p.kind, p.title, tags):
                self._seasonal.add(_key(p))
        self._cache: OrderedDict[tuple[str, date], tuple[Slot, ...]] = OrderedDict()

    # --- catalog lookups ------------------------------------------------------------------

    def _effective(
        self, show: str, eps: tuple[Program, ...], channel: str | None
    ) -> tuple[Program, ...]:
        dates = effective_dates(
            self.catalog,
            show,
            [(p.season or 0, p.episode or 0, p.air_date) for p in eps],
            first_year=self.library.show_years.get(show),
            channel=channel,
        )
        return tuple(replace(p, air_date=d) for p, d in zip(eps, dates, strict=True))

    def programs(self, show: str, channel: str | None = None) -> tuple[Program, ...]:
        """A show's on-disk episodes in air order, with effective air dates on `channel`
        (its synthetic premiere there, if any; None for the real or US debut dates)."""
        if channel is not None and (channel, show) in self._channel_programs:
            return self._channel_programs[(channel, show)]
        return self._programs.get(show, ())

    def movie(self, show: str, channel: str | None = None) -> Program | None:
        """A movie on disk, premiering on `channel`'s synthetic premiere if it has one."""
        found = self.library.movies.get(show)
        synthetic = self._premieres.get((channel, show)) if channel else None
        if found is not None and synthetic is not None:
            return replace(found, air_date=synthetic.premiere)
        return found

    def premiere(self, channel_id: str, show: str) -> date | None:
        """The synthetic premiere of a show on a channel, if one is set."""
        synthetic = self._premieres.get((channel_id, show))
        return synthetic.premiere if synthetic else None

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

    def _power_hour(
        self, channel_id: str, local: datetime, under: DayPart | None
    ) -> tuple[DayPart | None, datetime | None]:
        """The station manager's power hour (M5) on a channel at a local time, as a day-part
        ending at its UTC end, or (None, the UTC instant it starts later today, if it does).
        It takes `under`'s primetime flag, so NEW episodes and holiday specials keep to prime.
        """
        ph: PowerHour | None = self._power.get(channel_id)
        if ph is None:
            return None, None
        day = local.date()
        hour, minute = (int(x) for x in ph.start.split(":"))
        start = datetime.combine(day, time(hour, minute), tzinfo=self.tz)
        end_hour = hour + ph.hours
        end = (  # a power hour stops at midnight at the latest
            datetime.combine(day + timedelta(days=1), time(0), tzinfo=self.tz)
            if end_hour >= 24
            else datetime.combine(day, time(end_hour, minute), tzinfo=self.tz)
        )
        if local < start:
            return None, start.astimezone(UTC)
        if local >= end:
            return None, None
        part = DayPart(
            weekday=WEEKDAYS[day.weekday()],  # type: ignore[arg-type]
            start=ph.start,
            primetime=bool(under and under.primetime),
            pool=ph.pool,
        )
        return part, end.astimezone(UTC)

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

    def _candidates(
        self, channel: Channel, part: DayPart | None, day: date
    ) -> list[tuple[_Source, list[Program]]]:
        return [
            (s, progs)
            for s in self._pool(channel.id, part, day)
            if (progs := self._eligible(channel, s, day))
        ]

    def tags(self, program: Program) -> frozenset[str]:
        """Holiday ids a program is tagged for (see catalog/holidays.py)."""
        return self._tags.get(_key(program), frozenset())

    def _in_season(self, program: Program, day: date) -> bool:
        if _key(program) not in self._seasonal:
            return True
        return bool(self.tags(program) & self.tagger.on(day).season)

    def _eligible(self, channel: Channel, source: _Source, day: date) -> list[Program]:
        if source.kind == "movie":
            movie = self.movie(source.show, channel.id)
            if (
                movie
                and (movie.air_date is None or movie.air_date <= day)
                and self._in_season(movie, day)
            ):
                return [movie]
            return []
        # The debut rule holds on every channel, library and custom ones too: `library` only
        # keeps a channel on the dial before its launch (Brandon, M2). To air a show early on
        # purpose, give it a synthetic premiere on that channel.
        programs = self.programs(source.show, channel.id)
        found = [p for p in programs if p.air_date is not None and p.air_date <= day]
        if self._seasonal:
            found = [p for p in found if self._in_season(p, day)]
        return found

    def _holiday_label(self, program: Program, day: date) -> str | None:
        tags = self.tags(program)
        if not tags:
            return None
        # Labelled inside the holiday's window; seasonal items also across their season.
        cal = self.tagger.on(day)
        live = {h.id for h in cal.window}
        if _key(program) in self._seasonal:
            live |= cal.season
        return next((h for h in self.tagger.holidays if h in tags and h in live), None)

    def _prime(self, channel_id: str, part: DayPart | None, local: datetime) -> bool:
        if part is not None and part.primetime:
            return True
        template = self._template(channel_id, local.date())
        weekday = WEEKDAYS[local.date().weekday()]
        if template and any(p.primetime and p.weekday == weekday for p in template.parts):
            return False  # the template says where prime is
        now = local.time().replace(tzinfo=None)
        return DEFAULT_PRIME[0] <= now < DEFAULT_PRIME[1]

    def _holidays(
        self, channel: Channel, part: DayPart | None, local: datetime
    ) -> "_Holidays | None":
        prefer: set[str] = set()
        inject: set[str] = set()
        boost = 1.0
        for h in self.tagger.on(local.date()).window:
            mode = mode_for(h, channel.id)
            if mode == "off":
                continue
            inject.add(h.id)
            if mode == "on":
                prefer.add(h.id)
                boost = max(boost, STRENGTH_BOOST[h.strength])
        if not inject:
            return None
        return _Holidays(
            prefer=frozenset(prefer),
            inject=frozenset(inject),
            boost=boost,
            prime=self._prime(channel.id, part, local),
        )

    def _is_special(self, program: Program) -> bool:
        return program.kind == "movie" or _key(program) in self._seasonal

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
        played: set[ProgramKey] = set()  # programs already aired this broadcast day
        # Tagged episodes are preferred until each has aired once outside prime and once in
        # prime, so Halloween prime time stays themed without looping one episode all day.
        played_prime: set[ProgramKey] = set()
        marathon = self._marathon(channel, day)
        t = start
        while t < end:
            local = t.astimezone(self.tz)
            sim_date = local.date()
            part, boundary = self._active_part(channel.id, local)
            rng = Random(f"{self.seed}:{channel.id}:{t.isoformat()}")
            if marathon:
                t = self._marathon_step(channel, day, marathon, slots, t, end, rng)
                continue
            candidates = self._candidates(channel, part, sim_date)
            if not candidates and part is not None and part.block:
                # A block with nothing to air that day (before its first show, after its
                # last) gives way to the channel's regular shows instead of going Off Air.
                candidates = self._candidates(channel, None, sim_date)
            power, power_edge = self._power_hour(channel.id, local, part)
            if power is not None and power_edge is not None:
                # The power hour plays when one of its shows can air; else the usual part.
                found = self._candidates(channel, power, sim_date)
                if found:
                    part, boundary, candidates = power, power_edge, found
            elif power_edge is not None:
                boundary = min(boundary, power_edge)  # Off Air stops where the power hour begins
            holidays = self._holidays(channel, part, local)
            room = end - t
            special = None
            if holidays:
                special = self._special(channel, part, sim_date, holidays, room, played)
                candidates = self._hold_back(candidates, holidays)
            if not candidates and special is None:
                t = self._fill(slots, t, min(boundary, end), OFF_AIR)
                continue
            bumper = self._bumper(channel, rng) if slots and slots[-1].kind != "filler" else None
            pick: tuple[Program, bool] | None = (special, False) if special else None
            if pick is None:
                pick = self._pick(
                    channel, part, day, sim_date, candidates, room, rng, aired, slots,
                    holidays, played_prime if holidays and holidays.prime else played,
                )  # fmt: skip
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
            aired[program.show] += 1
            played.add(_key(program))
            if holidays and holidays.prime:
                played_prime.add(_key(program))
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
                    holiday=self._holiday_label(program, sim_date),
                )
            )
            t = slot_end
        return tuple(slots)

    def _marathon(self, channel: Channel, day: date) -> tuple[tuple[Program, str], ...]:
        """On a holiday itself, a movie channel's holiday marathon: every eligible movie it
        carries tagged for that holiday, in carriage order (the channel's curated order), with
        the holiday it airs for. Empty on any other day, or for any other kind of channel."""
        if channel.type != "movie":
            return ()
        today = [
            h
            for h in self.tagger.on(day).window
            if holiday_date(h, day.year) == day and mode_for(h, channel.id) != "off"
        ]
        found: dict[str, tuple[Program, str]] = {}
        for c in self._carriage.get(channel.id, []):
            movie = self.movie(c.show, channel.id)
            if (
                c.kind != "movie"
                or movie is None
                or c.show in found
                or c.show in self._excluded
                or not _carried(c, day)
                or (movie.air_date is not None and movie.air_date > day)
            ):
                continue
            label = next((h.id for h in today if h.id in self.tags(movie)), None)
            if label:
                found[c.show] = (movie, label)
        return tuple(found.values())

    def _marathon_step(
        self,
        channel: Channel,
        day: date,
        marathon: tuple[tuple[Program, str], ...],
        slots: list[Slot],
        t: datetime,
        end: datetime,
        rng: Random,
    ) -> datetime:
        """Place the next marathon movie (after a bumper) and return the new time. The marathon
        runs through the whole broadcast day, past midnight to 5 a.m.; each movie airs in full,
        and time too short for any of them becomes Paid Programming."""
        room = (end - t).total_seconds()
        aired = sum(1 for s in slots if s.kind == "movie")
        order = [marathon[(aired + i) % len(marathon)] for i in range(len(marathon))]
        last = next((s.show for s in reversed(slots) if s.kind == "movie"), None)
        fitting = [m for m in order if m[0].seconds <= room]
        if len(fitting) > 1 and fitting[0][0].show == last:
            fitting = fitting[1:] + fitting[:1]
        if not fitting:
            return self._fill(slots, t, end, PAID_PROGRAMMING)
        movie, holiday = fitting[0]
        bumper = self._bumper(channel, rng) if slots and slots[-1].kind != "filler" else None
        if bumper:
            gap = timedelta(seconds=min(bumper.seconds, channel.bumper_seconds))
            if movie.seconds + gap.total_seconds() <= room:
                slots.append(
                    Slot(start=t, end=t + gap, kind="bumper", title=channel.name, path=bumper.path)
                )
                t += gap
        slot_end = t + timedelta(seconds=movie.seconds)
        slots.append(
            Slot(
                start=t,
                end=slot_end,
                kind="movie",
                title=movie.title,
                show=movie.show,
                air_date=movie.air_date,
                transplant=self._is_transplant(channel, movie.show),
                path=movie.path,
                holiday=holiday,
            )
        )
        return slot_end

    def _special(
        self,
        channel: Channel,
        part: DayPart | None,
        day: date,
        holidays: "_Holidays",
        room: timedelta,
        played: set[ProgramKey],
    ) -> Program | None:
        """In prime time inside a window, the next holiday movie or special not yet aired
        today. Specials come from everything the channel carries outside blocks, plus the
        part's own pool, so Halloweentown reaches a primetime part that names only series."""
        if not holidays.prime:
            return None
        sources = {(s.show, s.kind): s for s in self._pool(channel.id, None, day)}
        for s in self._pool(channel.id, part, day):
            sources.setdefault((s.show, s.kind), s)
        found = [
            p
            for s in sources.values()
            for p in self._eligible(channel, s, day)
            if self._is_special(p)
            and self.tags(p) & holidays.inject
            and _key(p) not in played
            and p.seconds <= room.total_seconds()
        ]
        if not found:
            return None
        return min(found, key=lambda p: _stable_int(self.seed, channel.id, day, *_key(p)))

    def _hold_back(
        self, candidates: list[tuple[_Source, list[Program]]], holidays: "_Holidays"
    ) -> list[tuple[_Source, list[Program]]]:
        """Inside a window, holiday specials wait for prime time instead of airing whenever
        the dice say, unless nothing else is left to show."""
        kept = [
            (s, [p for p in progs if not (self._is_special(p) and self.tags(p) & holidays.inject)])
            for s, progs in candidates
        ]
        kept = [(s, progs) for s, progs in kept if progs]
        return kept or candidates

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
        """A show with a synthetic premiere on this channel, where the channel (or the entry's
        own `mark`) shows the transplant mark."""
        synthetic = self._premieres.get((channel.id, show))
        if synthetic is None:
            return False
        return channel.transplant_mark if synthetic.mark is None else synthetic.mark

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
        holidays: "_Holidays | None" = None,
        played: set[ProgramKey] | None = None,
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
            return self._next_in_order([progs[0] for _, progs in fitting], day, aired, slots), False

        # Inside an "on" window, a show's tagged episodes not yet aired today come first, and
        # shows that have some get a boost by the holiday's strength.
        preferred: dict[str, list[Program]] = {}
        if holidays and holidays.prefer:
            seen = played or set()
            for s, progs in fitting:
                if s.kind == "show":
                    keep = [
                        p for p in progs if self.tags(p) & holidays.prefer and _key(p) not in seen
                    ]
                    if keep:
                        preferred[s.show] = keep
        boost = holidays.boost if holidays else 1.0

        previous = next((s.show for s in reversed(slots) if s.show), None)
        weights = [
            s.weight
            * (0.25 if s.show == previous and len(fitting) > 1 else 1.0)
            * (boost if s.show in preferred else 1.0)
            for s, _ in fitting
        ]
        source, progs = rng.choices(fitting, weights=weights)[0]
        if source.kind == "movie":
            return progs[0], False
        progs = preferred.get(source.show, progs)
        offset = _stable_int(self.seed, channel.id, source.show) % len(progs)
        index = (offset + (day - EPOCH).days * RERUN_STRIDE + aired[source.show]) % len(progs)
        return progs[index], False

    def _next_in_order(
        self, movies: list[Program], day: date, aired: Counter[str], slots: list[Slot]
    ) -> Program:
        """The next movie of an ordered playlist (carriage order). Each day starts further along
        the list, by about a day's worth of movies. A movie that has not aired yet this broadcast
        day comes first, so with enough titles nothing repeats within the day, even when
        the fitting titles change near 5 a.m. or around holiday specials; once all have aired,
        the one aired longest ago comes next."""
        per_day = max(1, 86400 // max(1, sum(m.seconds for m in movies) // len(movies)))
        start = ((day - EPOCH).days * per_day + sum(aired.values())) % len(movies)
        order = movies[start:] + movies[:start]
        last_aired = {s.show: i for i, s in enumerate(slots) if s.kind == "movie"}
        fresh = [m for m in order if m.show not in last_aired]
        if fresh:
            return fresh[0]
        return min(order, key=lambda m: last_aired[m.show])

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
