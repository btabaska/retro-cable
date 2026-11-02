"""Cross-file checks the schemas can't express. Run with `python -m retro_cable.catalog <dir>`."""

import sys
from calendar import monthrange
from collections import defaultdict
from datetime import date
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from retro_cable.catalog.load import Catalog, CatalogError, load_catalog

FIRST_DAY = date(1990, 1, 1)
LAST_DAY = date(2010, 12, 31)
FOREVER = date.max


def _overlaps(periods: list[tuple[date, date]]) -> bool:
    return any(b[0] <= a[1] for a, b in pairwise(sorted(periods)))


def _check_channels(catalog: Catalog) -> list[str]:
    errors: list[str] = []
    file = catalog.channels
    try:
        ZoneInfo(file.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"channels: unknown timezone '{file.timezone}'")
    low, high = file.year_range
    if not (FIRST_DAY.year <= low <= high <= LAST_DAY.year):
        errors.append(f"channels: year_range must be within 1990-2010, got {low}-{high}")

    seen: set[str] = set()
    for ch in file.channels:
        if ch.id in seen:
            errors.append(f"channels: duplicate channel id '{ch.id}'")
        seen.add(ch.id)
        periods = [(i.from_, i.until or FOREVER) for i in ch.identity]
        if any(start > end for start, end in periods):
            errors.append(f"{ch.id}: an identity ends before it starts")
        if _overlaps(periods):
            errors.append(f"{ch.id}: identity periods overlap")
        if ch.launch and not any(s <= ch.launch <= e for s, e in periods):
            errors.append(f"{ch.id}: no identity on launch date {ch.launch}")
        if ch.type == "movie" and ch.playlist is None:
            errors.append(f"{ch.id}: movie channels need a playlist (ordered or shuffled)")
        if ch.type != "movie" and ch.playlist is not None:
            errors.append(f"{ch.id}: only movie channels have a playlist")
        block_ids = [b.id for b in ch.blocks]
        if len(block_ids) != len(set(block_ids)):
            errors.append(f"{ch.id}: duplicate block ids")
    return errors


def _check_holidays(catalog: Catalog, channel_ids: set[str]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for h in catalog.holidays.holidays:
        if h.id in seen:
            errors.append(f"holidays: duplicate holiday id '{h.id}'")
        seen.add(h.id)
        if h.rule.type == "fixed" and h.rule.day > monthrange(2000, h.rule.month)[1]:
            errors.append(f"{h.id}: no such date {h.rule.month}/{h.rule.day}")
        for ch in h.channels:
            if ch not in channel_ids:
                errors.append(f"{h.id}: unknown channel '{ch}'")
        for item in h.tagged:
            if item.channel and item.channel not in channel_ids:
                errors.append(f"{h.id}: tagged '{item.title}' has unknown channel '{item.channel}'")
    return errors


def _check_carriage(catalog: Catalog, channel_ids: set[str]) -> list[str]:
    errors: list[str] = []
    blocks = {ch.id: {b.id for b in ch.blocks} for ch in catalog.channels.channels}
    windows: dict[tuple[str, str], list[tuple[date, date]]] = defaultdict(list)
    for c in catalog.carriage.carriage:
        label = f"{c.show} on {c.channel}"
        if c.channel not in channel_ids:
            errors.append(f"{label}: unknown channel '{c.channel}'")
        elif c.block and c.block not in blocks[c.channel]:
            errors.append(f"{label}: unknown block '{c.block}' on {c.channel}")
        if c.until and c.until < c.from_:
            errors.append(f"{label}: until is before from")
        windows[(c.show, c.channel)].append((c.from_, c.until or FOREVER))
    for (show, channel), periods in windows.items():
        if _overlaps(periods):
            errors.append(f"{show} on {channel}: carriage windows overlap")
    return errors


def _check_shows(catalog: Catalog, holiday_ids: set[str]) -> list[str]:
    errors: list[str] = []
    carried = {c.show for c in catalog.carriage.carriage}
    carried_on = {(c.show, c.channel) for c in catalog.carriage.carriage}
    premieres: set[tuple[str, str]] = set()
    for s in catalog.synthetic.synthetic:
        if (s.show, s.channel) in premieres:
            errors.append(f"synthetic: '{s.show}' on {s.channel} is listed twice")
        premieres.add((s.show, s.channel))
        if (s.show, s.channel) not in carried_on:
            errors.append(f"synthetic: '{s.show}' is not carried on {s.channel}")
        if not FIRST_DAY <= s.premiere <= LAST_DAY:
            errors.append(f"{s.show} on {s.channel}: premiere must be within 1990-2010")
    seen: set[str] = set()
    for o in catalog.overrides.shows:
        if o.show in seen:
            errors.append(f"overrides: '{o.show}' is listed twice")
        seen.add(o.show)
        if o.show not in carried:
            errors.append(f"overrides: '{o.show}' has no carriage on any channel")
        for ep, tags in o.holiday_tags.items():
            errors.extend(
                f"{o.show} {ep}: unknown holiday '{t}'" for t in tags if t not in holiday_ids
            )
    return errors


def _check_templates(catalog: Catalog) -> list[str]:
    errors: list[str] = []
    channels = {c.id: c for c in catalog.channels.channels}
    carried: dict[str, set[str]] = defaultdict(set)  # channel -> show ids of kind "show"
    tagged: dict[str, set[str]] = defaultdict(set)  # channel -> carriage tags used on that channel
    for c in catalog.carriage.carriage:
        if c.kind == "show":
            carried[c.channel].add(c.show)
        tagged[c.channel].update(c.tags)
    blocks = {c.id: {b.id for b in c.blocks} for c in catalog.channels.channels}
    seen: set[str] = set()
    eras: dict[str, list[tuple[date, date]]] = defaultdict(list)
    for t in catalog.templates.templates:
        if t.id in seen:
            errors.append(f"templates: duplicate template id '{t.id}'")
        seen.add(t.id)
        if t.channel not in channels:
            errors.append(f"templates: '{t.id}' has unknown channel '{t.channel}'")
            continue
        if channels[t.channel].type == "movie":
            errors.append(f"templates: '{t.id}' targets movie channel '{t.channel}'")
        if t.until and t.until < t.from_:
            errors.append(f"{t.id}: until is before from")
        eras[t.channel].append((t.from_, t.until or FOREVER))
        starts: dict[str, list[str]] = defaultdict(list)
        for p in t.parts:
            if p.block and p.block not in blocks[t.channel]:
                errors.append(f"{t.id}: unknown block '{p.block}' on {t.channel}")
            for entry in p.pool:
                if entry.show and entry.show not in carried[t.channel]:
                    errors.append(f"{t.id}: pool show '{entry.show}' is not carried on {t.channel}")
                if entry.tag and entry.tag not in tagged[t.channel]:
                    errors.append(f"{t.id}: pool tag '{entry.tag}' unused in {t.channel} carriage")
            starts[p.weekday].append(p.start)
        for weekday, times in starts.items():
            if len(times) != len(set(times)):
                errors.append(f"{t.id}: {weekday} has two parts starting at the same time")
            if min(times) != "00:00":
                errors.append(f"{t.id}: {weekday} must have a part starting at 00:00")
    for channel, periods in eras.items():
        if _overlaps(periods):
            errors.append(f"{channel}: template era periods overlap")
    return errors


def validate(catalog: Catalog) -> list[str]:
    """Every problem in a loaded catalog, as human-readable lines; empty means valid."""
    channel_ids = {c.id for c in catalog.channels.channels}
    holiday_ids = {h.id for h in catalog.holidays.holidays}
    return [
        *_check_channels(catalog),
        *_check_holidays(catalog, channel_ids),
        *_check_carriage(catalog, channel_ids),
        *_check_shows(catalog, holiday_ids),
        *_check_templates(catalog),
    ]


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "catalog")
    try:
        catalog = load_catalog(root)
    except CatalogError as err:
        print(err, file=sys.stderr)
        return 1
    errors = validate(catalog)
    for line in errors:
        print(line, file=sys.stderr)
    if not errors:
        print(f"catalog {catalog.version}: ok")
    return 1 if errors else 0
