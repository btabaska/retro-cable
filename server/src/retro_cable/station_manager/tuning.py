"""The station manager's guardrails (M5). The model proposes; this code decides what is allowed.

A *knob* is one weight the station manager may turn: a show's carriage on a channel (the
all-day rotation, tag pools and empty block parts), or a show's entry in the parts of a
template that start at one time (the same day-part on every weekday). Its value is a factor
on the catalog's own weight, kept in the admin overlay's `tuning`, so the YAML and /admin
weights stay what they are and the drift is always measured from them.

The rules, all enforced here and never left to the model:
- weights only: a factor on an existing carriage row or pool entry, or a power hour of shows
  the channel already carries. Nothing is carried or dropped, and no date, launch, premiere
  or holiday moves (`inputs_only` checks it);
- the authenticity dial caps the drift: a factor stays within MAX_FACTOR ** (dial / 100) of
  1 either way (0: no change at all; 30: about x1.6; 100: x5), and always within 0.2-5;
- at most a fifth of a channel's knobs (rounded up) change in one run, the biggest moves
  first;
- a power hour pools at most 4 of the channel's most watched shows (10 minutes or more, at
  least half finished), weights 1-3, and only on channels with a dial above 0 that aren't
  movie channels.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel

from retro_cable.catalog import Catalog
from retro_cable.catalog.models import CarriageTune, PoolTune, PowerHour, Tuning
from retro_cable.station_manager.signals import Signals

MAX_FACTOR = 5.0
MIN_FACTOR = 0.2
CHANGE_SHARE = 0.2  # "at most about a fifth of slots change per week"
POWER_SHOWS = 4
POWER_CANDIDATES = 6
POWER_WEIGHT = (1.0, 3.0)
POWER_MIN_SECONDS = 600  # a show needs 10 minutes watched on the channel to join its power hour
POWER_MIN_COMPLETION = 0.5  # and to be mostly finished: top shows, not ones flipped past


@dataclass(frozen=True)
class Knob:
    id: str
    channel: str
    show: str
    base: float  # the catalog's weight (YAML or /admin), before tuning
    factor: float  # the current factor (1.0: untuned)
    template: str | None = None
    start: str | None = None


def drift_cap(authenticity: int) -> float:
    """How far a factor may move from 1 either way: 1 at dial 0, MAX_FACTOR at dial 100."""
    return float(MAX_FACTOR ** (max(0, min(100, authenticity)) / 100))


def knobs(catalog: Catalog, current: Tuning) -> dict[str, Knob]:
    """Every knob in a catalog without tuning (`catalog` is the effective catalog with the
    tuning taken out), with the factors `current` has for them."""
    carriage = {(t.channel, t.show): t.factor for t in current.carriage}
    pools = {(t.template, t.start, t.show): t.factor for t in current.pools}
    out: dict[str, Knob] = {}
    for c in catalog.carriage.carriage:
        kid = f"carriage:{c.channel}:{c.show}"
        if kid not in out:
            out[kid] = Knob(kid, c.channel, c.show, c.weight, carriage.get((c.channel, c.show), 1))
    for t in catalog.templates.templates:
        for p in t.parts:
            for e in p.pool:
                if e.show is None:
                    continue
                kid = f"pool:{t.id}:{p.start}:{e.show}"
                if kid not in out:
                    factor = pools.get((t.id, p.start, e.show), 1.0)
                    out[kid] = Knob(kid, t.channel, e.show, e.weight, factor, t.id, p.start)
    return dict(sorted(out.items()))


class Change(BaseModel):
    knob: str
    channel: str
    show: str
    title: str = ""
    template: str | None = None
    start: str | None = None
    before: float
    after: float
    why: str = ""


class PowerHourPlan(BaseModel):
    channel: str
    start: str
    hours: int
    shows: list[str]
    titles: list[str] = []
    weights: list[float]


class Dropped(BaseModel):
    what: str
    channel: str | None = None
    reason: str


class Proposed(BaseModel):
    """What the model asked for, already checked to name real knobs and shows."""

    factors: dict[str, float]
    why: dict[str, str] = {}
    power: dict[str, list[tuple[str, float]]] = {}  # channel -> [(show, weight)]


@dataclass
class Guarded:
    tuning: Tuning
    changes: list[Change] = field(default_factory=list)
    power_hours: list[PowerHourPlan] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)

    def channels(self, before: Tuning) -> list[str]:
        """Channels whose tuning this run changes."""
        out = {c.channel for c in self.changes}
        old = {p.channel: p for p in before.power_hours}
        new = {p.channel: p for p in self.tuning.power_hours}
        out |= {c for c in old.keys() | new.keys() if old.get(c) != new.get(c)}
        return sorted(out)


def power_candidates(
    catalog: Catalog, signals: Signals, limit: int = POWER_CANDIDATES
) -> dict[str, list[str]]:
    """Per channel that can have a power hour, its most watched shows that it carries."""
    kinds = {c.id: c for c in catalog.channels.channels}
    carried = {(c.channel, c.show) for c in catalog.carriage.carriage if c.kind == "show"}
    out: dict[str, list[str]] = {}
    for s in signals.shows:  # most watched first
        ch = kinds.get(s.channel)
        if ch is None or ch.type == "movie" or ch.authenticity == 0:
            continue
        if (s.channel, s.show) not in carried or s.seconds < POWER_MIN_SECONDS:
            continue
        if s.completion < POWER_MIN_COMPLETION:
            continue
        shows = out.setdefault(s.channel, [])
        if len(shows) < limit:
            shows.append(s.show)
    return out


def _round(x: float) -> float:
    return round(x, 2)


def guard(
    proposed: Proposed,
    catalog: Catalog,
    current: Tuning,
    signals: Signals,
    window: tuple[str, int] | None,
) -> Guarded:
    """The tuning this run may apply: `current` with the allowed part of `proposed`."""
    table = knobs(catalog, current)
    dials = {c.id: c.authenticity for c in catalog.channels.channels}
    titles = {c.show: c.title for c in catalog.carriage.carriage}
    out = Guarded(tuning=current)
    wanted: dict[str, list[Change]] = {}
    for kid, factor in sorted(proposed.factors.items()):
        knob = table.get(kid)
        if knob is None:
            out.dropped.append(Dropped(what=kid, reason="no such weight"))
            continue
        cap = drift_cap(dials.get(knob.channel, 30))
        allowed = max(1 / cap, MIN_FACTOR, min(cap, MAX_FACTOR, factor))
        why = proposed.why.get(kid, "")
        if _round(allowed) != _round(factor):
            why = f"{why} (capped at x{_round(allowed)} by authenticity {dials.get(knob.channel)})"
        after = _round(allowed)
        if math.isclose(after, _round(knob.factor), abs_tol=0.005):
            continue
        wanted.setdefault(knob.channel, []).append(
            Change(
                knob=kid, channel=knob.channel, show=knob.show, title=titles.get(knob.show, ""),
                template=knob.template,
                start=knob.start, before=_round(knob.factor), after=after, why=why.strip(),
            )
        )  # fmt: skip
    per_channel = {k.channel for k in table.values()}
    counts = {c: sum(1 for k in table.values() if k.channel == c) for c in per_channel}
    for channel, changes in sorted(wanted.items()):
        limit = math.ceil(counts[channel] * CHANGE_SHARE)
        changes.sort(key=lambda c: (-abs(math.log(c.after / c.before)), c.knob))
        out.changes += changes[:limit]
        out.dropped += [
            Dropped(
                what=c.knob,
                channel=channel,
                reason=f"more than a fifth of {channel}'s {counts[channel]} weights would"
                " change this week",
            )
            for c in changes[limit:]
        ]
    out.changes.sort(key=lambda c: c.knob)
    out.power_hours, dropped = _power(proposed, catalog, signals, window)
    out.dropped += dropped
    out.tuning = _merge(current, out.changes, table, out.power_hours)
    return out


def _power(
    proposed: Proposed, catalog: Catalog, signals: Signals, window: tuple[str, int] | None
) -> tuple[list[PowerHourPlan], list[Dropped]]:
    if window is None:
        return [], []
    candidates = power_candidates(catalog, signals)
    titles = {c.show: c.title for c in catalog.carriage.carriage}
    plans: list[PowerHourPlan] = []
    dropped: list[Dropped] = []
    for channel, picks in sorted(proposed.power.items()):
        allowed = candidates.get(channel)
        if not allowed:
            dropped.append(
                Dropped(what="power hour", channel=channel, reason="no watched show it can pool")
            )
            continue
        shows: list[str] = []
        weights: list[float] = []
        for show, weight in picks:
            if show in allowed and show not in shows and len(shows) < POWER_SHOWS:
                shows.append(show)
                weights.append(_round(max(POWER_WEIGHT[0], min(POWER_WEIGHT[1], weight))))
        if shows:
            start, hours = window
            plans.append(
                PowerHourPlan(
                    channel=channel, start=start, hours=hours, shows=shows, weights=weights,
                    titles=[titles.get(s, s) for s in shows],
                )
            )  # fmt: skip
    return plans, dropped


def _merge(
    current: Tuning,
    changes: Iterable[Change],
    table: Mapping[str, Knob],
    power: list[PowerHourPlan],
) -> Tuning:
    """`current` with the changes applied; factors back at 1 are left out. The power hours
    are this run's (a channel the model left out has none this week)."""
    factors = {k.id: k.factor for k in table.values() if k.factor != 1}
    factors |= {c.knob: c.after for c in changes}
    carriage, pools = [], []
    for kid, factor in sorted(factors.items()):
        if math.isclose(factor, 1.0) or kid not in table:
            continue
        k = table[kid]
        if k.template is None:
            carriage.append(CarriageTune(channel=k.channel, show=k.show, factor=factor))
        else:
            assert k.start is not None
            pools.append(PoolTune(template=k.template, start=k.start, show=k.show, factor=factor))
    hours = [
        PowerHour.model_validate(
            {
                "channel": p.channel,
                "start": p.start,
                "hours": p.hours,
                "pool": [{"show": s, "weight": w} for s, w in zip(p.shows, p.weights, strict=True)],
            }
        )
        for p in power
    ]
    return Tuning(carriage=carriage, pools=pools, power_hours=hours)


def for_channels(tuning: Tuning, keep: set[str], catalog: Catalog) -> Tuning:
    """Only the parts of `tuning` on these channels."""
    owner = {t.id: t.channel for t in catalog.templates.templates}
    return Tuning(
        carriage=[t for t in tuning.carriage if t.channel in keep],
        pools=[t for t in tuning.pools if owner.get(t.template) in keep],
        power_hours=[p for p in tuning.power_hours if p.channel in keep],
    )


def combine(a: Tuning, b: Tuning) -> Tuning:
    return Tuning(
        carriage=[*a.carriage, *b.carriage],
        pools=[*a.pools, *b.pools],
        power_hours=[*a.power_hours, *b.power_hours],
    )


def inputs_only(before: Catalog, after: Catalog) -> list[str]:
    """Problems if `after` differs from `before` in anything but weights and power hours:
    which shows are carried where and when, channels (launches included), premieres,
    overrides (debuts), holidays and the shape of every template. Empty means inputs only."""
    problems: list[str] = []

    def rows(c: Catalog) -> list[object]:
        return [x.model_copy(update={"weight": 1.0}) for x in c.carriage.carriage]

    def parts(c: Catalog) -> list[object]:
        return [
            t.model_copy(
                update={
                    "parts": [
                        p.model_copy(
                            update={"pool": [e.model_copy(update={"weight": 1.0}) for e in p.pool]}
                        )
                        for p in t.parts
                    ]
                }
            )
            for t in c.templates.templates
        ]

    if rows(before) != rows(after):
        problems.append("carriage changed beyond weights")
    if parts(before) != parts(after):
        problems.append("templates changed beyond weights")
    for name in ("channels", "synthetic", "overrides", "holidays"):
        if getattr(before, name) != getattr(after, name):
            problems.append(f"{name} changed")
    return problems
