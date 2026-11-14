"""One run of the weekly station manager (M5).

It reads the last few weeks of watch sessions, turns them into signals (signals.py), asks the
rig's local model for a diff, keeps only what the guardrails allow (tuning.py), runs the
/admin checks on every channel it touches, and then applies the result as one overlay
revision (auto-apply on), records it as a proposal (off), or records why it did nothing.
Every run is recorded (runs.py). It changes inputs only: weights and power hours in the
admin overlay's `tuning`, never a schedule, the YAML, carriage, dates or holidays.
"""

import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from retro_cable.admin import store
from retro_cable.admin.drafts import preview_week, smoke_test
from retro_cable.catalog import Catalog, validate
from retro_cable.catalog.models import Tuning
from retro_cable.catalog.overlay import Overlay, apply_overlay, with_tuning
from retro_cable.channel_builder.llm import LLM, InvalidReply, LLMError, Transcript, ask_json
from retro_cable.channel_builder.proposal import safe_day
from retro_cable.scheduler import Library, Scheduler
from retro_cable.station_manager import runs
from retro_cable.station_manager.runs import PowerWindow, RunReport, StationRun, Status
from retro_cable.station_manager.signals import (
    MIN_TOTAL_SECONDS,
    Signals,
    compute,
    load_sessions,
    power_window,
)
from retro_cable.station_manager.tuning import (
    CHANGE_SHARE,
    POWER_SHOWS,
    Dropped,
    Guarded,
    Knob,
    Proposed,
    combine,
    drift_cap,
    for_channels,
    guard,
    inputs_only,
    knobs,
    power_candidates,
)

DEFAULT_YEAR = 2000  # the year the checks run in when no session says otherwise


def auto_apply() -> bool:
    """RC_STATION_AUTO_APPLY: on unless set to 0 (or false, no, off)."""
    value = os.environ.get("RC_STATION_AUTO_APPLY", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class Options:
    weeks: int = 4
    auto_apply: bool = True
    dry_run: bool = False
    seed: int = 0


# --- the model's reply ---------------------------------------------------------------------


class KnobChange(BaseModel):
    knob: str
    factor: float = Field(gt=0, le=100)
    why: str = Field(default="", max_length=300)


class PowerPick(BaseModel):
    show: str
    weight: float = Field(default=1.0, gt=0, le=100)


class PowerChannel(BaseModel):
    channel: str
    shows: list[PowerPick] = Field(max_length=10)


class Reply(BaseModel):
    summary: str = Field(default="", max_length=500)
    changes: list[KnobChange] = Field(default_factory=list, max_length=200)
    power_hour: list[PowerChannel] = Field(default_factory=list, max_length=100)


SYSTEM = """You are the station manager of Retro Cable, a recreation of 1990-2010 Bay Area \
cable TV for one household. Each week you read what the household watched and propose small \
changes to how often shows air on each channel. You only change weights: you never add or \
remove shows, and you never change dates. Reply with one JSON object only, no prose."""


def _hm(seconds: int) -> str:
    h, m = divmod(round(seconds / 60), 60)
    return f"{h} h {m} min" if h else f"{m} min"


def _clock(hour: int) -> str:
    if hour in (0, 24):
        return "midnight"
    return f"{(hour - 1) % 12 + 1} {'AM' if hour < 12 else 'PM'}"


def prompt(
    signals: Signals,
    catalog: Catalog,
    table: dict[str, Knob],
    window: tuple[str, int] | None,
    candidates: dict[str, list[str]],
    week: date,
    weeks: int,
) -> str:
    """The user prompt: the signals per watched channel and the knobs the model may turn.
    Deterministic, so a run can be replayed."""
    titles = {c.show: c.title for c in catalog.carriage.carriage}
    channels = {c.id: c for c in catalog.channels.channels}
    by_channel: dict[str, list[Knob]] = {}
    for k in table.values():
        by_channel.setdefault(k.channel, []).append(k)
    lines = [
        f"Week of {week.isoformat()}. The household watched {_hm(signals.total_seconds)} over"
        f" the last {weeks} weeks ({signals.sessions} programs), mostly in"
        f" {signals.year or DEFAULT_YEAR}.",
        "Busiest viewing hours (real time, "
        f"{catalog.channels.timezone}): {', '.join(_clock(h) for h in signals.busiest_hours)}.",
        "",
        "Rules:",
        "- Raise the weight of shows the household finishes (high completion); lower shows it"
        " skips (early tune-aways, low completion). Leave shows with little viewing alone.",
        "- A weight is a factor on the channel's own weight: 1 means as the channel was"
        " programmed. Keep each factor inside its channel's allowed range.",
        "- Change only a few weights per channel, never more than the channel's limit.",
    ]
    if window is not None and candidates:
        start, hours = window
        end = int(start[:2]) + hours
        lines.append(
            f"- Power hour, {_clock(int(start[:2]))} to {_clock(end)} every day: for each"
            f" channel with power hour choices, pick up to {POWER_SHOWS} of those shows, each"
            " with a weight from 1 to 3, or leave the channel out."
        )
    lines += ["", "Viewing per channel:"]
    show_sig = {(s.channel, s.show): s for s in signals.shows}
    for cs in signals.channels:
        ch = channels.get(cs.channel)
        mine = sorted(by_channel.get(cs.channel, []), key=lambda k: k.id)
        if ch is None or not mine:
            continue
        cap = drift_cap(ch.authenticity)
        limit = math.ceil(len(mine) * CHANGE_SHARE)
        lines.append(
            f"{ch.id} ({ch.name}; authenticity {ch.authenticity}, factors"
            f" {1 / cap:.2f}-{cap:.2f}, at most {limit} changes): {_hm(cs.seconds)},"
            f" {cs.sessions} programs, average stay {_hm(cs.mean_seconds)}"
        )
        for s in (x for x in signals.shows if x.channel == cs.channel):
            lines.append(
                f'  {s.show} "{titles.get(s.show, s.show)}": {_hm(s.seconds)}, {s.sessions}'
                f" programs, {round(s.completion * 100)}% finished, {s.early_tune_aways} early"
                " tune-aways"
            )
        lines.append(f"Weights you can change on {ch.id}:")
        for k in mine:
            where = "all day" if k.template is None else f"part from {k.start}"
            seen = "" if (k.channel, k.show) in show_sig else ", not watched"
            lines.append(
                f"  {k.id}  {titles.get(k.show, k.show)}, {where}: weight {k.base:g},"
                f" factor now {k.factor:g}{seen}"
            )
        if window is not None and cs.channel in candidates:
            lines.append(f"Power hour choices on {ch.id}: {', '.join(candidates[cs.channel])}")
    lines += [
        "",
        "Reply with JSON:",
        '{"summary": "one line", "changes": [{"knob": "<id>", "factor": 1.3, "why": "short'
        ' reason"}], "power_hour": [{"channel": "<id>", "shows": [{"show": "<id>",'
        ' "weight": 2}]}]}',
    ]
    return "\n".join(lines) + "\n"


def check_reply(reply: Reply, table: dict[str, Knob], candidates: dict[str, list[str]]) -> None:
    """Names the model must get right; numbers are the guardrails' job, not a retry's."""
    problems = [f"unknown knob {c.knob}" for c in reply.changes if c.knob not in table]
    for pc in reply.power_hour:
        allowed = candidates.get(pc.channel)
        if allowed is None:
            problems.append(f"{pc.channel} has no power hour choices")
            continue
        problems += [
            f"{p.show} is not a power hour choice on {pc.channel}"
            for p in pc.shows
            if p.show not in allowed
        ]
    if problems:
        raise InvalidReply("\n".join(problems[:12]))


# --- a run ----------------------------------------------------------------------------------


def week_of(now: datetime, tz: ZoneInfo) -> date:
    """The Sunday on or before the run's local date."""
    day = now.astimezone(tz).date()
    return day - timedelta(days=(day.weekday() + 1) % 7)


def check_channels(
    base: Catalog,
    head: Overlay,
    current: Tuning,
    proposed: Tuning,
    changed: list[str],
    library: Library,
    seed: int,
    week: list[date],
) -> tuple[Tuning, dict[str, list[str]]]:
    """Each changed channel's new tuning through the /admin checks, one channel at a time on
    top of the ones before: inputs only, the catalog's validate() and a week's smoke test.
    A channel that fails keeps its current tuning. Returns the tuning that passed and the
    problems per failed channel."""
    untuned = apply_overlay(base, with_tuning(head, Tuning()))
    everything = {c.id for c in untuned.channels.channels}
    accepted = for_channels(current, everything - set(changed), untuned)
    failed: dict[str, list[str]] = {}
    for channel in changed:
        trial = combine(accepted, for_channels(proposed, {channel}, untuned))
        merged = apply_overlay(base, with_tuning(head, trial))
        problems = inputs_only(untuned, merged) + validate(merged)
        if not problems:
            sched = Scheduler(merged, library, seed=seed)
            problems = smoke_test(sched, merged.channel(channel), week)
        if problems:
            failed[channel] = problems
            accepted = combine(accepted, for_channels(current, {channel}, untuned))
        else:
            accepted = trial
    return accepted, failed


def _drop_failed(guarded: Guarded, failed: dict[str, list[str]]) -> Guarded:
    if not failed:
        return guarded
    return Guarded(
        tuning=guarded.tuning,
        changes=[c for c in guarded.changes if c.channel not in failed],
        power_hours=[p for p in guarded.power_hours if p.channel not in failed],
        dropped=guarded.dropped
        + [
            Dropped(what="every change", channel=ch, reason=f"failed the checks: {p[0]}")
            for ch, p in sorted(failed.items())
        ],
    )


def describe(guarded: Guarded) -> str:
    parts = []
    if guarded.changes:
        n = len({c.channel for c in guarded.changes})
        parts.append(
            f"{len(guarded.changes)} weight{'s' * (len(guarded.changes) != 1)} on {n}"
            f" channel{'s' * (n != 1)}"
        )
    if guarded.power_hours:
        p = guarded.power_hours[0]
        n = len(guarded.power_hours)
        parts.append(f"power hour {p.start} ({p.hours} h) on {n} channel{'s' * (n != 1)}")
    return ", ".join(parts) or "no power hour this week (last week's is gone)"


def run(
    db: sqlite3.Connection,
    base: Catalog,
    library: Library,
    llm: LLM,
    now: datetime,
    opts: Options,
) -> StationRun:
    tz = ZoneInfo(base.channels.timezone)
    head = store.head(db)
    current = head.overlay.tuning
    untuned = apply_overlay(base, with_tuning(head.overlay, Tuning()))
    week = week_of(now, tz).isoformat()
    since = now - timedelta(weeks=opts.weeks)
    signals = compute(load_sessions(db, since, now), tz)
    report = RunReport(weeks=opts.weeks, since=since, until=now, signals=signals, before=current)

    def done(status: Status, summary: str, revision: int | None = None) -> StationRun:
        return runs.record(db, now, week, status, llm.model, summary, report, revision)

    if signals.total_seconds < MIN_TOTAL_SECONDS:
        return done(
            "skipped",
            f"Not enough viewing yet: {_hm(signals.total_seconds)} in {opts.weeks} weeks"
            f" (it needs {_hm(MIN_TOTAL_SECONDS)}). Nothing changed.",
        )

    window = power_window(signals)
    if window is not None:
        report.power_window = PowerWindow(start=window[0], hours=window[1])
    candidates = power_candidates(untuned, signals)
    watched = {c.channel for c in signals.channels}
    offered = {k: v for k, v in knobs(untuned, current).items() if v.channel in watched}
    text = prompt(signals, untuned, offered, window, candidates, week_of(now, tz), opts.weeks)
    log = Transcript()
    try:
        reply = ask_json(
            llm, SYSTEM, text, Reply,
            check=lambda r: check_reply(r, offered, candidates), transcript=log,
        )  # fmt: skip
    except LLMError as e:
        report.notes.append(str(e))
        return done("failed", f"The local model couldn't be reached: {e}. Nothing changed.")
    if reply is None:
        report.notes += log.failures
        return done(
            "failed",
            f"The model's replies were still invalid after {log.retries} retries. Nothing changed.",
        )
    report.model_summary = reply.summary
    proposed = Proposed(
        factors={c.knob: c.factor for c in reply.changes},
        why={c.knob: c.why for c in reply.changes},
        power={pc.channel: [(p.show, p.weight) for p in pc.shows] for pc in reply.power_hour},
    )
    guarded = guard(proposed, untuned, current, signals, window)
    year = signals.year or DEFAULT_YEAR
    local = now.astimezone(tz)
    days = preview_week(safe_day(year, local.month, local.day))
    changed = guarded.channels(current)
    final, failed = check_channels(
        base, head.overlay, current, guarded.tuning, changed, library, opts.seed, days
    )
    guarded = _drop_failed(guarded, failed)
    report.changes, report.power_hours, report.dropped = (
        guarded.changes,
        guarded.power_hours,
        guarded.dropped,
    )
    report.after = final
    if final == current:
        return done("skipped", "Nothing to change this week.")
    what = describe(guarded)
    if opts.dry_run:
        return done("dry_run", f"Dry run: would change {what}. Nothing changed.")
    if not opts.auto_apply:
        return done("proposed", f"Proposed {what}; auto-apply is off, so nothing changed.")
    note = f"station manager: week of {week}, {what}"
    try:
        rev = store.save(db, with_tuning(head.overlay, final), note, now, expect_head=head.id)
    except store.Conflict:
        report.notes.append("the catalog changed in /admin while the station manager ran")
        return done("failed", "The catalog changed while it ran; nothing applied. Run again.")
    return done("applied", f"Applied {what}. Undo in /admin.", rev.id)


class NotApplied(Exception):
    pass


def apply_run(
    db: sqlite3.Connection, base: Catalog, library: Library, run_id: int, now: datetime, seed: int
) -> store.Revision:
    """Apply a proposed (or dry) run after the fact: only while the tuning is still the one it
    started from, and only if every changed channel passes the checks again."""
    found = runs.get(db, run_id)
    after = found.report.after
    if found.status not in ("proposed", "dry_run") or after is None:
        raise NotApplied(f"run {run_id} is {found.status}; only proposals can be applied")
    head = store.head(db)
    if head.overlay.tuning != found.report.before:
        raise NotApplied("the weights changed since this run; run the station manager again")
    tz = ZoneInfo(base.channels.timezone)
    year = found.report.signals.year or DEFAULT_YEAR
    local = now.astimezone(tz)
    days = preview_week(safe_day(year, local.month, local.day))
    changed = sorted(
        {c.channel for c in found.report.changes}
        | {p.channel for p in [*after.power_hours, *found.report.before.power_hours]}
    )
    _, failed = check_channels(
        base, head.overlay, head.overlay.tuning, after, changed, library, seed, days
    )
    if failed:
        channel, problems = sorted(failed.items())[0]
        raise NotApplied(f"{channel} fails the checks now: {problems[0]}")
    note = f"station manager: week of {found.week_of} (run {run_id})"
    return store.save(db, with_tuning(head.overlay, after), note, now, expect_head=head.id)
