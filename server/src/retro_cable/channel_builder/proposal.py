"""A channel builder proposal: what it is, the deterministic rules that shape it, and how it is
checked and applied.

A proposal changes inputs only. It is applied as one admin overlay revision (catalog/
overlay.py): new custom channels with their carriage, pretend premieres and day-part
templates, and carriage (plus premieres) added to existing catalog channels. It goes through
the same checks as an edit in /admin (admin/drafts.py: field checks, the catalog's cross-file
validation and a week's schedule smoke test). Pieces that fail are reported and left out;
nothing is applied without the explicit `apply` command, and one undo reverts all of it.
"""

import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from retro_cable.admin import store
from retro_cable.admin.drafts import (
    FIRST_YEAR,
    LAST_YEAR,
    ChannelDraft,
    DraftShow,
    LibraryItem,
    check_draft,
    check_fields,
    preview_week,
    smoke_test,
    to_models,
)
from retro_cable.catalog import Catalog, validate
from retro_cable.catalog.models import CUSTOM_PREFIX, Template
from retro_cable.catalog.overlay import Overlay, apply_overlay
from retro_cable.channel_builder.shows import ShowRow
from retro_cable.scheduler import Library, Scheduler

ChannelType = Literal["network", "marathon", "movie"]
Days = Literal["weekdays", "weekends", "all"]
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
WEEKENDS = ("saturday", "sunday")
DAY_GROUPS: dict[str, tuple[str, ...]] = {
    "weekdays": WEEKDAYS,
    "weekends": WEEKENDS,
    "all": WEEKDAYS + WEEKENDS,
}
HHMM = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
# Where each audience's new channels go on the dial; the next free number from the band's
# start, skipping every number any channel uses in any year.
NUMBER_BANDS = {
    "preschool": 12,
    "kids": 20,
    "family": 32,
    "teen": 50,
    "adult": 60,
    "anime": 80,
    "movies": 90,
    "unknown": 100,
}
CALL_SIGN_MAX = 6
MARATHON_MAX = 6
DEFAULT_PREMIERE_DAY = (9, 15)  # mid-September, a fall season premiere


class ProposedShow(BaseModel):
    show: str = Field(description="Library id (catalog slug)")
    title: str
    kind: Literal["show", "movie"]
    category: str
    debut: date | None = Field(description="Real (or US debut) first air date, if known")
    from_year: int = Field(description="First year this channel carries it")
    premiere: date | None = Field(
        default=None, description="Pretend premiere on this channel (shows debuting after 2010)"
    )
    on_nas: bool = False
    in_library: bool = Field(default=False, description="Known to the server's library")


class DayPartPlan(BaseModel):
    name: str
    days: Days
    start: str = Field(pattern=HHMM)
    shows: list[str] = Field(description="Show ids from this channel")
    primetime: bool = False


class ProposedChannel(BaseModel):
    id: str = Field(description="custom-… for a new channel, else the catalog channel's id")
    reuse: bool = Field(description="An existing channel: only carriage and premieres are added")
    name: str
    call_sign: str
    number: int
    type: ChannelType
    launch_year: int | None = Field(description="None: on the dial in every year")
    audience: str
    identity: str = Field(description="The channel's audience, genre and feel, in a sentence")
    shows: list[ProposedShow]
    dayparts: list[DayPartPlan] = Field(default_factory=list)


class Unplaced(BaseModel):
    show: str
    title: str
    reason: str


class AlreadyCarried(BaseModel):
    show: str
    title: str
    channels: list[str]


class Issue(BaseModel):
    severity: Literal["error", "warning"]
    channel: str | None = None
    show: str | None = None
    message: str


class Proposal(BaseModel):
    format: Literal[1] = 1
    created_at: str
    model: str = Field(description="The local model that drafted it (LiteLLM alias)")
    shows_file: str
    base_version: str = Field(description="Effective catalog version it was drafted against")
    channels: list[ProposedChannel]
    already_carried: list[AlreadyCarried] = Field(default_factory=list)
    unplaced: list[Unplaced] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --- deterministic rules -------------------------------------------------------------------


def clamp_year(year: int) -> int:
    return max(FIRST_YEAR, min(LAST_YEAR, year))


def safe_day(year: int, month: int, day: int) -> date:
    """The date, with Feb 29 falling back to Feb 28 outside leap years."""
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)


def premiere_year(row: ShowRow, suggested: int | None = None) -> int:
    """A post-2010 show's pretend premiere year: the model's suggestion when it is inside
    1990-2010, else twenty years before the real debut (2014 -> 1994), clamped."""
    if suggested is not None and FIRST_YEAR <= suggested <= LAST_YEAR:
        return suggested
    return clamp_year((row.debut_year or LAST_YEAR + 1) - 20)


def pretend_premiere(row: ShowRow, year: int) -> date:
    """Same month and day as the real debut when the list has a full date, else Sep 15."""
    first = row.first_aired
    if first is not None and (first.month, first.day) != (1, 1):
        return safe_day(year, first.month, first.day)
    return safe_day(year, *DEFAULT_PREMIERE_DAY)


def start_year(row: ShowRow, premiere: date | None) -> int:
    """The first year a show can air at all: its pretend premiere, else its debut."""
    if premiere is not None:
        return premiere.year
    return clamp_year(row.debut_year or FIRST_YEAR)


def launch_year(suggested: int | None, show_years: Iterable[int]) -> int:
    """A new channel launches by the year its earliest show can air, or earlier if the model
    placed it earlier: never later, so no show waits for the channel."""
    earliest = min(show_years, default=LAST_YEAR)
    if suggested is not None and FIRST_YEAR <= suggested <= LAST_YEAR:
        return min(suggested, earliest)
    return earliest


def taken_numbers(catalog: Catalog) -> set[int]:
    return {i.number for c in catalog.channels.channels for i in c.identity}


def taken_call_signs(catalog: Catalog) -> set[str]:
    return {i.call_sign.upper() for c in catalog.channels.channels for i in c.identity}


def next_number(audience: str, taken: set[int]) -> int:
    n = NUMBER_BANDS.get(audience, NUMBER_BANDS["unknown"])
    while n in taken:
        n += 1
    if n > 999:
        n = next(i for i in range(1, 1000) if i not in taken)
    taken.add(n)
    return n


def call_sign(suggested: str, name: str, taken: set[str]) -> str:
    """The model's call sign cleaned to up to six letters or digits, else the name's
    initials; a digit is added while it collides."""
    base = re.sub(r"[^A-Za-z0-9]", "", suggested).upper()[:CALL_SIGN_MAX]
    if not base:
        words = re.findall(r"[A-Za-z0-9]+", name)
        base = "".join(w[0] for w in words).upper()[:CALL_SIGN_MAX] or "CH"
        if len(base) < 3 and words:
            base = re.sub(r"[^A-Z0-9]", "", words[0].upper())[:4] or base
    sign, n = base, 2
    while sign in taken:
        suffix = str(n)
        sign, n = base[: CALL_SIGN_MAX - len(suffix)] + suffix, n + 1
    taken.add(sign)
    return sign


def templates_for(channel: ProposedChannel) -> list[Template]:
    """A network channel's day-parts as one template, from the first year every part has a
    show that can air (the all-day rotation before that). Every weekday gets a
    00:00 part (all of its shows) unless the plan has one; shows the plan leaves out join the
    00:00 parts, so everything carried can air. Marathon and movie channels get none (the
    default all-day rotation)."""
    if channel.type != "network" or not channel.dayparts or channel.reuse:
        return []
    carried = [s.show for s in channel.shows if s.kind == "show"]
    if not carried:
        return []
    # (weekday, start) -> (primetime, pool of show ids); the first plan for a slot wins.
    parts: dict[tuple[str, str], tuple[bool, list[str]]] = {}
    named: set[str] = set()
    for plan in channel.dayparts:
        pool = [s for s in dict.fromkeys(plan.shows) if s in carried]
        if not pool:
            continue
        named.update(pool)
        for weekday in DAY_GROUPS[plan.days]:
            parts.setdefault((weekday, plan.start), (plan.primetime, pool))
    if not parts:
        return []
    leftover = [s for s in carried if s not in named]
    for weekday in DAY_GROUPS["all"]:
        key = (weekday, "00:00")
        if key not in parts:
            parts[key] = (False, carried)
        elif leftover:
            prime, pool = parts[key]
            parts[key] = (prime, [*pool, *leftover])
    order = DAY_GROUPS["all"]
    ordered = [
        {"weekday": w, "start": t, "primetime": prime, "pool": [{"show": s} for s in pool]}
        for (w, t), (prime, pool) in sorted(
            parts.items(), key=lambda kv: (order.index(kv[0][0]), kv[0][1])
        )
    ]
    # The template starts once every part has a show that can air; before that the channel
    # plays everything it carries all day (no template covers those years), never Off Air.
    years = {s.show: s.from_year for s in channel.shows}
    ready = max((min(years[x] for x in pool) for _, pool in parts.values()), default=FIRST_YEAR)
    start = date(max(channel.launch_year or FIRST_YEAR, ready), 1, 1)
    return [
        Template.model_validate(
            {"id": f"{channel.id}-week", "channel": channel.id, "from": start, "parts": ordered}
        )
    ]


def draft_for(channel: ProposedChannel) -> ChannelDraft:
    """A new channel as the admin form's draft, so it takes the same checks and models."""
    launch = channel.launch_year
    return ChannelDraft(
        number=channel.number,
        call_sign=channel.call_sign,
        name=channel.name,
        type=channel.type,
        library=launch is None,
        launch_year=launch,
        shows=[
            DraftShow(
                show=s.show,
                kind=s.kind,
                from_year=None if s.from_year == (launch or FIRST_YEAR) else s.from_year,
                premiere=s.premiere,
            )
            for s in channel.shows
        ],
    )


# --- checks and apply ----------------------------------------------------------------------


def with_list_items(index: Mapping[str, LibraryItem], proposal: Proposal) -> dict[str, LibraryItem]:
    """The server's library plus the proposal's shows it doesn't know yet (not on disk or
    not scanned), with the list's debut date. The checks then warn that they have no playable
    files rather than refuse them: carrying a show is what makes the rig convert it."""
    out = dict(index)
    for ch in proposal.channels:
        for s in ch.shows:
            out.setdefault(s.show, LibraryItem(s.show, s.title, s.kind, s.debut, None, 0, 0))
    return out


@dataclass
class Checked:
    overlay: Overlay  # the head overlay with every valid channel applied
    issues: list[Issue] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)  # channel ids that passed
    skipped: list[str] = field(default_factory=list)  # channel ids left out, with errors

    @property
    def ok(self) -> bool:
        return not self.skipped


def check_proposal(
    proposal: Proposal,
    base: Catalog,
    head: Overlay,
    index: Mapping[str, LibraryItem],
    library: Library,
    seed: int,
    year: int = 2000,
) -> Checked:
    """Every channel through the /admin checks, in order, each on top of the ones before it.
    A channel with errors is skipped (and reported); the rest still apply."""
    items = with_list_items(index, proposal)
    out = Checked(overlay=head)
    shows_placed: set[str] = set()
    for ch in proposal.channels:
        shows_placed.update(s.show for s in ch.shows)
        issues = (
            _check_reuse(ch, base, out.overlay, items, library, seed, year)
            if ch.reuse
            else _check_new(ch, base, out.overlay, items, library, seed, year)
        )
        overlay, found = issues
        out.issues += found
        if overlay is None:
            out.skipped.append(ch.id)
        else:
            out.overlay = overlay
            out.applied.append(ch.id)
    carried = {a.show for a in proposal.already_carried}
    unplaced = {u.show for u in proposal.unplaced}
    for dup in sorted(shows_placed & unplaced):
        out.issues.append(Issue(severity="warning", show=dup, message="placed and unplaced"))
    for dup in sorted(carried & unplaced):
        out.issues.append(Issue(severity="warning", show=dup, message="carried and unplaced"))
    return out


def _week(year: int, launch: int | None) -> date:
    return date(max(year, launch or FIRST_YEAR), 11, 1)


def _issues(
    channel: str, severity: Literal["error", "warning"], found: Iterable[object]
) -> list[Issue]:
    out = []
    for f in found:
        message = getattr(f, "message", str(f))
        out.append(Issue(severity=severity, channel=channel, message=str(message)))
    return out


def _check_new(
    ch: ProposedChannel,
    base: Catalog,
    head: Overlay,
    items: Mapping[str, LibraryItem],
    library: Library,
    seed: int,
    year: int,
) -> tuple[Overlay | None, list[Issue]]:
    if not ch.id.startswith(CUSTOM_PREFIX):
        return None, [
            Issue(severity="error", channel=ch.id, message="new channel ids start with custom-")
        ]
    current = apply_overlay(base, head)
    if any(c.id == ch.id for c in current.channels.channels):
        return None, [Issue(severity="error", channel=ch.id, message=f"{ch.id} already exists")]
    issues: list[Issue] = []
    if ch.type == "marathon" and len(ch.shows) > MARATHON_MAX:
        issues.append(
            Issue(
                severity="warning",
                channel=ch.id,
                message=f"A marathon channel with {len(ch.shows)} shows; marathons are meant"
                " for one or a few.",
            )
        )
    templates = templates_for(ch)
    checked = check_draft(
        base,
        head,
        ch.id,
        draft_for(ch),
        items,
        library,
        seed,
        _week(year, ch.launch_year),
        templates,
    )
    issues += _issues(ch.id, "error", checked.result.errors)
    issues += _issues(ch.id, "warning", checked.result.warnings)
    return checked.overlay, issues


def _check_reuse(
    ch: ProposedChannel,
    base: Catalog,
    head: Overlay,
    items: Mapping[str, LibraryItem],
    library: Library,
    seed: int,
    year: int,
) -> tuple[Overlay | None, list[Issue]]:
    current = apply_overlay(base, head)
    try:
        existing = current.channel(ch.id)
    except KeyError:
        return None, [Issue(severity="error", channel=ch.id, message=f"no channel {ch.id}")]
    already = {c.show for c in current.carriage.carriage if c.channel == ch.id}
    new = [s for s in ch.shows if s.show not in already]
    if not new:
        return head, []
    ident = sorted(existing.identity, key=lambda i: i.from_)[-1]
    launch = existing.launch.year if existing.launch else None
    draft = ChannelDraft(
        number=ident.number,
        call_sign=ident.call_sign,
        name=existing.name,
        type=existing.type,
        library=existing.library or launch is None,
        launch_year=launch,
        shows=[
            DraftShow(
                show=s.show,
                kind=s.kind,
                from_year=None if s.from_year <= (launch or FIRST_YEAR) else s.from_year,
                premiere=s.premiere,
            )
            for s in new
        ],
    )
    errors, warnings = check_fields(ch.id, draft, current, items)
    # Only the shows are new here; the channel's own fields come from the YAML as they are.
    errors = [e for e in errors if (e.field or "").startswith("shows.")]
    issues = _issues(ch.id, "error", errors) + _issues(ch.id, "warning", warnings)
    if errors:
        return None, issues
    _, carriage, premieres = to_models(ch.id, draft, items)
    replaced = {(p.show, p.channel) for p in premieres}
    overlay = Overlay(
        channels=head.channels,
        carriage=[*head.carriage, *carriage],
        premieres=[*(p for p in head.premieres if (p.show, p.channel) not in replaced), *premieres],
        templates=head.templates,
    )
    merged = apply_overlay(base, overlay)
    problems = validate(merged)
    if not problems:
        sched = Scheduler(merged, library, seed=seed)
        problems = smoke_test(sched, merged.channel(ch.id), preview_week(_week(year, launch)))
    if problems:
        return None, [*issues, *_issues(ch.id, "error", problems)]
    return overlay, issues


class NotApplied(Exception):
    def __init__(self, checked: Checked) -> None:
        super().__init__(f"{len(checked.skipped)} channel(s) failed the checks")
        self.checked = checked


def apply_proposal(
    db: sqlite3.Connection,
    proposal: Proposal,
    base: Catalog,
    index: Mapping[str, LibraryItem],
    library: Library,
    seed: int,
    now: datetime,
    skip_invalid: bool = False,
) -> tuple[store.Revision, Checked]:
    """Check the proposal against the database's head overlay and save the result as one
    revision (undo reverts it whole). Refuses when a channel fails, unless `skip_invalid`,
    which applies the rest."""
    head = store.head(db).overlay
    checked = check_proposal(proposal, base, head, index, library, seed)
    if not checked.applied or (checked.skipped and not skip_invalid):
        raise NotApplied(checked)
    shows = sum(len(c.shows) for c in proposal.channels if c.id in checked.applied)
    note = f"channel builder: {len(checked.applied)} channels, {shows} shows"
    return store.save(db, checked.overlay, note, now), checked
