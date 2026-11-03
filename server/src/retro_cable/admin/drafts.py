"""Channel drafts from the admin form: conversion to catalog models, and the checks run before
anything is saved (field checks, the catalog's cross-file validation, and a one-week schedule
smoke test on the same engine)."""

import itertools
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from retro_cable.catalog import Catalog, validate
from retro_cable.catalog.models import Carriage, Channel, Identity, Synthetic, Template
from retro_cable.catalog.overlay import CUSTOM_PREFIX, Overlay, apply_overlay, with_channel
from retro_cable.scheduler import Library, Scheduler

FIRST_YEAR, LAST_YEAR = 1990, 2010
FIRST_DAY, LAST_DAY = date(FIRST_YEAR, 1, 1), date(LAST_YEAR, 12, 31)
CALL_SIGN_MAX = 6


class DraftShow(BaseModel):
    show: str = Field(description="Library id (catalog slug) of a show or movie")
    kind: Literal["show", "movie"] = "show"
    from_year: int | None = Field(default=None, description="First year carried; default launch")
    until_year: int | None = Field(default=None, description="Last year carried; default open")
    premiere: date | None = Field(
        default=None,
        description="Pretend premiere on this channel only (1990-01-01 to 2010-12-31)",
    )


class ChannelDraft(BaseModel):
    """What the admin form edits. Checked by POST /admin/check before any save."""

    number: int
    call_sign: str
    name: str
    type: Literal["network", "marathon", "movie"] = "network"
    library: bool = Field(
        default=False, description="On the dial in every year; episodes still wait for their dates"
    )
    launch_year: int | None = Field(default=None, description="Required unless library")
    shows: list[DraftShow] = Field(default_factory=list)


class CheckIssue(BaseModel):
    field: str | None = Field(
        description="Form field: number, call_sign, name, launch_year, shows, shows.<i> or"
        " shows.<i>.premiere"
    )
    message: str


class CheckResult(BaseModel):
    ok: bool
    errors: list[CheckIssue]
    warnings: list[CheckIssue]
    catalog_version: str | None = Field(description="The version this draft would produce")


@dataclass(frozen=True)
class LibraryItem:
    id: str
    title: str
    kind: Literal["show", "movie"]
    first_air: date | None  # first and last playable effective date (no pretend premiere)
    last_air: date | None
    episodes: int  # playable: normalized, or under the media root with a runtime
    known: int  # all known, on disk or not


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-") or "channel"


def new_channel_id(name: str, taken: set[str]) -> str:
    base = CUSTOM_PREFIX + slugify(name)
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _start(draft: ChannelDraft) -> date:
    if draft.library or draft.launch_year is None:
        return date(FIRST_YEAR, 1, 1)
    return date(draft.launch_year, 1, 1)


def to_models(
    channel_id: str, draft: ChannelDraft, library: Mapping[str, LibraryItem]
) -> tuple[Channel, list[Carriage], list[Synthetic]]:
    """The draft as catalog models. Call after check_fields() has no errors."""
    start = _start(draft)
    channel = Channel.model_validate(
        {
            "id": channel_id,
            "name": draft.name.strip(),
            "type": draft.type,
            "launch": None if draft.library else start,
            "library": draft.library,
            "playlist": "shuffled" if draft.type == "movie" else None,
            "identity": [
                {"from": start, "number": draft.number, "call_sign": draft.call_sign.upper()}
            ],
        }
    )
    carriage = [
        Carriage.model_validate(
            {
                "show": s.show,
                "title": library[s.show].title if s.show in library else s.show,
                "channel": channel_id,
                "from": max(start, date(s.from_year, 1, 1)) if s.from_year else start,
                "until": date(s.until_year, 12, 31) if s.until_year else None,
                "kind": s.kind,
            }
        )
        for s in draft.shows
    ]
    premieres = [
        Synthetic(show=s.show, channel=channel_id, premiere=s.premiere)
        for s in draft.shows
        if s.premiere is not None
    ]
    return channel, carriage, premieres


def premiere_issues(
    title: str,
    premiere: date,
    debut: date | None,
    on_dial_from: date | None,
    until: date | None,
) -> tuple[list[str], list[str]]:
    """Plain-words (errors, warnings) for a pretend premiere."""
    errors: list[str] = []
    warnings: list[str] = []
    if not FIRST_DAY <= premiere <= LAST_DAY:
        errors.append("Pick a date from 1990 to 2010.")
    elif until is not None and premiere > until:
        errors.append("This is after the show leaves the channel, so it would never air.")
    if not errors:
        if on_dial_from is not None and premiere < on_dial_from:
            warnings.append(
                f"{title}: the channel isn't on the dial until {on_dial_from.year}."
                " The show starts then."
            )
        if debut is not None and premiere > debut:
            warnings.append(
                f"{title}: this is later than its real premiere, so it will air later than it did."
            )
    return errors, warnings


def to_draft(
    channel: Channel, carriage: Sequence[Carriage], premieres: Sequence[Synthetic] = ()
) -> ChannelDraft:
    dates = {p.show: p.premiere for p in premieres if p.channel == channel.id}
    ident = sorted(channel.identity, key=lambda i: i.from_)[-1]
    launch = channel.launch.year if channel.launch else None
    return ChannelDraft(
        number=ident.number,
        call_sign=ident.call_sign,
        name=channel.name,
        type=channel.type,
        library=channel.library,
        launch_year=launch,
        shows=[
            DraftShow(
                show=c.show,
                kind=c.kind,
                from_year=c.from_.year if c.from_.year != (launch or FIRST_YEAR) else None,
                until_year=c.until.year if c.until else None,
                premiere=dates.get(c.show),
            )
            for c in carriage
            if c.channel == channel.id
        ],
    )


def _periods_overlap(a: Identity, start: date) -> bool:
    return a.until is None or a.until >= start


def check_fields(
    channel_id: str,
    draft: ChannelDraft,
    catalog: Catalog,
    library: Mapping[str, LibraryItem],
) -> tuple[list[CheckIssue], list[CheckIssue]]:
    """Form-level checks with plain-words messages. `catalog` is the effective catalog without
    this channel. Returns (errors, warnings)."""
    errors: list[CheckIssue] = []
    warnings: list[CheckIssue] = []

    def err(where: str | None, message: str) -> None:
        errors.append(CheckIssue(field=where, message=message))

    if not draft.name.strip():
        err("name", "Give the channel a name.")
    sign = draft.call_sign.strip()
    if not sign:
        err("call_sign", "Give the channel a call sign.")
    elif len(sign) > CALL_SIGN_MAX or not re.fullmatch(r"[A-Za-z0-9]+", sign):
        err("call_sign", f"Call signs are up to {CALL_SIGN_MAX} letters or digits.")
    if not 1 <= draft.number <= 999:
        err("number", "Channel numbers run from 1 to 999.")
    if not draft.library:
        if draft.launch_year is None:
            err("launch_year", "Pick the year it went on the air, or make it a library channel.")
        elif not FIRST_YEAR <= draft.launch_year <= LAST_YEAR:
            err(
                "launch_year",
                f"Channel launches {draft.launch_year}; pick a year from {FIRST_YEAR} to"
                f" {LAST_YEAR}, or make it a library channel.",
            )
    start = _start(draft)
    for other in catalog.channels.channels:
        if other.id == channel_id:
            continue
        for ident in other.identity:
            if ident.number == draft.number and _periods_overlap(ident, start):
                when = "" if ident.from_.year <= start.year else f" from {ident.from_.year}"
                err("number", f"Number {draft.number} is already {other.name}{when}.")
                break
        else:
            continue
        break
    if not draft.shows:
        err("shows", "Add at least one show.")
    seen: set[str] = set()
    for i, s in enumerate(draft.shows):
        where = f"shows.{i}"
        item = library.get(s.show)
        if item is None:
            err(where, f"{s.show} isn't in the library.")
            continue
        if s.show in seen:
            err(where, f"{item.title} is on the channel twice.")
        seen.add(s.show)
        if item.kind != s.kind:
            err(where, f"{item.title} is a {item.kind}, not a {s.kind}.")
        for year in (s.from_year, s.until_year):
            if year is not None and not FIRST_YEAR <= year <= LAST_YEAR:
                err(where, f"{item.title}: years run from {FIRST_YEAR} to {LAST_YEAR}.")
        if s.from_year and s.until_year and s.until_year < s.from_year:
            err(where, f"{item.title}: the last year is before the first.")
        if s.premiere is not None:
            until = date(s.until_year, 12, 31) if s.until_year else None
            dial = None if draft.library else (date(s.from_year, 1, 1) if s.from_year else start)
            bad, notes = premiere_issues(item.title, s.premiere, item.first_air, dial, until)
            errors.extend(CheckIssue(field=f"{where}.premiere", message=m) for m in bad)
            warnings.extend(CheckIssue(field=f"{where}.premiere", message=m) for m in notes)
        if item.episodes == 0:
            # Raw files from `ingest scan` play once the normalize job, which encodes every
            # carried show, has run.
            warnings.append(
                CheckIssue(field=where, message=f"{item.title} has no playable files yet.")
            )
        elif s.premiere is not None:
            pass  # the pretend premiere sets its dates on this channel
        elif item.first_air is None and s.kind == "show":
            warnings.append(
                CheckIssue(
                    field=where,
                    message=f"{item.title} has no air dates yet, so it won't air."
                    " Set a pretend premiere.",
                )
            )
        elif item.first_air:
            carried_from = date(s.from_year, 1, 1) if s.from_year else start
            last = date(s.until_year, 12, 31) if s.until_year else LAST_DAY
            kind = "premiere" if s.kind == "movie" else "debut"
            if item.first_air > last:
                warnings.append(
                    CheckIssue(
                        field=where,
                        message=f"{item.title} debuts {item.first_air:%b %Y}, so it won't air"
                        " here. Set a pretend premiere.",
                    )
                )
            elif item.first_air > carried_from:
                warnings.append(
                    CheckIssue(
                        field=where,
                        message=f"{item.title} won't air before its {item.first_air.year} {kind}.",
                    )
                )
    return errors, warnings


def preview_week(day: date) -> list[date]:
    """Sunday through Saturday of the week containing `day`."""
    sunday = day - timedelta(days=(day.weekday() + 1) % 7)
    return [sunday + timedelta(days=i) for i in range(7)]


def smoke_test(sched: Scheduler, channel: Channel, days: Sequence[date]) -> list[str]:
    """The scheduler invariants on one channel for some days: no gaps or overlaps, every day
    closes at 5 a.m., and nothing airs before its date (on every channel, library ones too)."""
    problems = []
    for day in days:
        if not sched.on_air(channel, day):
            continue
        slots = sched.day(channel.id, day)
        start = datetime.combine(day, time(5), sched.tz)
        end = datetime.combine(day + timedelta(days=1), time(5), sched.tz)
        label = day.strftime("%a %b %-d %Y")
        if slots[0].start != start or slots[-1].end != end:
            problems.append(f"{label}: the day doesn't run 5 a.m. to 5 a.m.")
        for a, b in itertools.pairwise(slots):
            if a.end != b.start:
                problems.append(f"{label}: gap or overlap at {a.end.astimezone(sched.tz):%H:%M}.")
                break
        for s in slots:
            aired = s.start.astimezone(sched.tz).date()
            if s.kind in ("episode", "movie") and (s.air_date is None or s.air_date > aired):
                problems.append(f"{label}: {s.title} would air before its first air date.")
                break
    return problems


def keep_templates(
    templates: Sequence[Template], draft: ChannelDraft
) -> tuple[list[Template], list[CheckIssue]]:
    """A custom channel's day-part templates (from the channel builder) after an edit in
    /admin: kept while every show they name is still on the channel, else dropped, so the
    channel falls back to the all-day rotation, with a warning saying so."""
    if not templates:
        return [], []
    carried = {s.show for s in draft.shows if s.kind == "show"}
    named = {e.show for t in templates for p in t.parts for e in p.pool if e.show}
    if draft.type != "movie" and named <= carried:
        return list(templates), []
    return [], [
        CheckIssue(
            field="shows",
            message="Its day-parts named a show that is no longer on the channel, so the"
            " channel now plays everything it carries all day.",
        )
    ]


@dataclass(frozen=True)
class Checked:
    result: CheckResult
    catalog: Catalog | None  # the effective catalog with the draft, when it passed
    overlay: Overlay | None
    channel: Channel | None
    scheduler: Scheduler | None = None  # on the effective catalog, when it passed


def check_draft(
    base: Catalog,
    head: Overlay,
    channel_id: str,
    draft: ChannelDraft,
    library_index: Mapping[str, LibraryItem],
    library: Library,
    seed: int,
    week_of: date,
    templates: Sequence[Template] = (),
) -> Checked:
    """`templates` are the channel's day-parts, checked and saved with it (none: the default
    all-day rotation)."""
    current = apply_overlay(base, head)
    errors, warnings = check_fields(channel_id, draft, current, library_index)
    if errors:
        failed = CheckResult(ok=False, errors=errors, warnings=warnings, catalog_version=None)
        return Checked(failed, None, None, None)
    channel, carriage, premieres = to_models(channel_id, draft, library_index)
    overlay = with_channel(head, channel, carriage, premieres, list(templates))
    merged = apply_overlay(base, overlay)
    errors += [CheckIssue(field=None, message=m) for m in validate(merged)]
    sched = None
    if not errors:
        sched = Scheduler(merged, library, seed=seed)
        errors += [
            CheckIssue(field=None, message=m)
            for m in smoke_test(sched, channel, preview_week(week_of))
        ]
    ok = not errors
    return Checked(
        CheckResult(
            ok=ok, errors=errors, warnings=warnings, catalog_version=merged.version if ok else None
        ),
        merged if ok else None,
        overlay if ok else None,
        channel,
        sched if ok else None,
    )
