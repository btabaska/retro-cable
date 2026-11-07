"""Prune the YAML catalog to Brandon's show list (M4):
`python -m retro_cable.channel_builder.lineup prune --shows CSV [--catalog DIR]
[--same SHOW=TITLE ...] [--keep SHOW ...] [--dry-run]`.

The channel builder adds shows to channels but never takes any away, so the YAML catalog is
pruned to the list first. Every carried show is matched against the list's rows (title and
`aka`), and kept only when it matches a `keep` or `add` row:

1. `--same SHOW=TITLE` and `--keep SHOW` are judgment calls made by hand; they come first.
2. The same title: the normalized title (as `ingest scan` compares them) of a carriage title or
   overrides.yaml `plex_title` equals a row's title or one of its `aka` names, with or without
   a trailing `(1998)`; else the same with spaces ignored (`YuYu Hakusho`). A `keep`/`add`
   row keeps the show; only `remove` rows remove it.
3. A variant: no row has the title, but exactly one kept row of the same kind starts with it
   or is its start, word for word (`Digimon` and `Digimon: Digital Monsters`). Several such
   rows keep the show without ids (`ambiguous variant`) until `--same` picks one.
4. Anything else is not in the list and is removed.

Removed shows leave carriage.yaml, synthetic.yaml, overrides.yaml (validation requires a
carried show there) and the template pools. A part whose pool loses every show takes the
template's remaining shows (a part with a channel block keeps the block while it still carries
something); a template with no shows left is dropped, so its channel gets the default all-day
rotation. Comment headers of sections that lose every entry go with them. Kept shows get the
list's TMDB id (`tmdb_id`) and first-aired year (`year`) in overrides.yaml; values already
there are kept. Running it again changes nothing. The result is validated before anything is
written; channels are never removed, even when they carry nothing any more.
"""

import argparse
import csv
import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from retro_cable.catalog import Catalog, CatalogError, load_catalog, validate
from retro_cable.catalog.load import _Loader
from retro_cable.catalog.models import TMDB_REF
from retro_cable.ingest.plex import normalize_title

KEEP = frozenset({"keep", "add"})
IDS_HEADER = "# TMDB ids and first-aired years from Brandon's lineup (channel_builder.lineup prune)"
_YEAR_SUFFIX = re.compile(r"\s*\((?:19|20)\d{2}\)\s*$")
_ITEM = re.compile(r"^(\s*)- ")
_BARE = re.compile(r"^[a-z][a-z0-9-]*$")
_YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}


@dataclass(frozen=True)
class ListRow:
    action: str
    title: str
    kind: str  # show, movie or special
    tmdb_id: str | None  # well formed (tv:N / movie:N), else None
    year: int | None  # first aired
    names: tuple[str, ...]  # title and aka names

    @property
    def kept(self) -> bool:
        return self.action in KEEP


def read_list(path: Path) -> list[ListRow]:
    """Every row of the show list CSV, `remove` rows included."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = []
        for r in csv.DictReader(f):
            title = (r.get("title") or "").strip()
            if not title:
                continue
            aka = [a.strip() for a in (r.get("aka") or "").split(";") if a.strip()]
            ref = (r.get("tmdb_id") or "").strip()
            year = (r.get("first_aired") or "").strip()[:4]
            rows.append(
                ListRow(
                    action=(r.get("action") or "").strip().lower(),
                    title=title,
                    kind=(r.get("kind") or "show").strip().lower(),
                    tmdb_id=ref if re.fullmatch(TMDB_REF, ref) else None,
                    year=int(year) if year.isdigit() and 1900 <= int(year) <= 2100 else None,
                    names=(title, *aka),
                )
            )
        return rows


@dataclass(frozen=True)
class Decision:
    show: str
    title: str  # the carriage title
    keep: bool
    how: str
    row: ListRow | None = None  # the row it matched; a kept show takes its ids
    note: str = ""

    @property
    def fuzzy(self) -> bool:
        """A match someone should look at: anything but the same title or aka name."""
        return self.how not in ("title", "aka", "remove row", "not in the list")


def _keys(title: str) -> set[str]:
    return {k for k in (normalize_title(title), normalize_title(_YEAR_SUFFIX.sub("", title))) if k}


def _compact(keys: Iterable[str]) -> set[str]:
    return {k.replace(" ", "") for k in keys}


def _kind_fits(carried: str, row: ListRow) -> bool:
    return row.kind == "show" if carried == "show" else row.kind in ("movie", "special")


def decide(
    catalog: Catalog,
    rows: list[ListRow],
    same: Mapping[str, str] | None = None,
    keep: Iterable[str] = (),
) -> list[Decision]:
    """Keep or remove each carried show (see the module docstring), in carriage order."""
    same = same or {}
    by_hand = set(keep)
    plex = {o.show: o.plex_title for o in catalog.overrides.shows if o.plex_title}
    titles: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for c in catalog.carriage.carriage:
        titles.setdefault(c.show, [])
        if c.title not in titles[c.show]:
            titles[c.show].append(c.title)
        kinds.setdefault(c.show, c.kind)
    for show, title in plex.items():
        if show in titles:
            titles[show].append(title)

    out: list[Decision] = []
    for show, names in titles.items():
        title = names[0]
        if show in same:
            wanted = normalize_title(same[show])
            row = next((r for r in rows if wanted in {normalize_title(n) for n in r.names}), None)
            if row is None:
                raise ValueError(f"--same {show}: no row titled '{same[show]}' in the list")
            how = "same as" if row.kept else "same as a remove row"
            out.append(Decision(show, title, row.kept, how, row, f"'{row.title}'"))
            continue
        if show in by_hand:
            out.append(Decision(show, title, True, "kept by hand"))
            continue
        out.append(_match(show, title, names, kinds[show], rows))
    return out


def _match(show: str, title: str, names: list[str], kind: str, rows: list[ListRow]) -> Decision:
    keys = set().union(*(_keys(n) for n in names))
    for exact, label in ((keys, ""), (_compact(keys), " (spacing)")):
        hits: list[tuple[ListRow, bool]] = []  # (row, matched on its title)
        for r in rows:
            for i, name in enumerate(r.names):
                theirs = _keys(name) if not label else _compact(_keys(name))
                if theirs & exact:
                    hits.append((r, i == 0))
                    break
        if not hits:
            continue
        kept = [(r, on_title) for r, on_title in hits if r.kept]
        if kept:
            row, on_title = sorted(kept, key=lambda h: not h[1])[0]
            how = ("title" if on_title else "aka") + label
            return Decision(show, title, True, how, row, f"'{row.title}'" if label else "")
        return Decision(show, title, False, "remove row" + label, hits[0][0],
                        f"'{hits[0][0].title}'" if label else "")  # fmt: skip

    variants = []
    for r in rows:
        if not r.kept or not _kind_fits(kind, r):
            continue
        theirs = set().union(*(_keys(n) for n in r.names))
        if any(t.startswith(k + " ") or k.startswith(t + " ") for t in theirs for k in keys):
            variants.append(r)
    if len(variants) == 1:
        return Decision(show, title, True, "variant", variants[0], f"'{variants[0].title}'")
    if variants:
        note = ", ".join(f"'{r.title}'" for r in variants)
        return Decision(show, title, True, "ambiguous variant", None, note)
    return Decision(show, title, False, "not in the list")


# --- editing the YAML text -----------------------------------------------------------------


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _load_item(lines: list[str]) -> dict[str, Any]:
    data = yaml.load("\n".join(lines), Loader=_Loader)  # a SafeLoader
    assert isinstance(data, list) and isinstance(data[0], dict)
    return data[0]


@dataclass
class _ListRegion:
    """The items of a top-level `key:` sequence: [(start, end)) line spans."""

    key_line: int
    end: int  # first line after the region
    items: list[tuple[int, int]]
    item_indent: int


def _region(lines: list[str], key: str) -> _ListRegion | None:
    key_line = next((i for i, x in enumerate(lines) if re.match(rf"^{key}:", x)), None)
    if key_line is None:
        return None
    end = next(
        (
            i
            for i in range(key_line + 1, len(lines))
            if lines[i].strip() and not lines[i].startswith((" ", "#"))
        ),
        len(lines),
    )
    return _ListRegion(key_line, end, *_spans(lines, key_line + 1, end))


def _spans(lines: list[str], start: int, end: int) -> tuple[list[tuple[int, int]], int]:
    """Sequence items between two lines, at the indentation of the first one."""
    first = next((i for i in range(start, end) if _ITEM.match(lines[i])), None)
    if first is None:
        return [], 2
    indent = _indent(lines[first])
    spans: list[tuple[int, int]] = []
    i = first
    while i < end:
        m = _ITEM.match(lines[i])
        if m and len(m[1]) == indent:
            j = i + 1
            while j < end and lines[j].strip() and _indent(lines[j]) > indent:
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans, indent


def _drop(lines: list[str], region: _ListRegion, gone: set[int]) -> list[str]:
    """The lines without the items starting at `gone`, and without comment-only section
    headers whose items all went; blank lines don't pile up."""
    starts = {s: e for s, e in region.items}
    removed: set[int] = set()
    for s in gone:
        removed.update(range(s, starts[s]))
    # Sections: runs of non-blank lines inside the region.
    i = region.key_line + 1
    while i < region.end:
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < region.end and lines[j].strip():
            j += 1
        items = [s for s in starts if i <= s < j]
        rest = [k for k in range(i, j) if not any(s <= k < starts[s] for s in items)]
        if (
            items
            and all(s in gone for s in items)
            and all(lines[k].lstrip().startswith("#") for k in rest)
        ):
            removed.update(range(i, j))
        i = j
    kept = [x for k, x in enumerate(lines) if k not in removed]
    out: list[str] = []
    for x in kept:
        if not x.strip() and out and not out[-1].strip():
            continue
        out.append(x)
    while out and not out[-1].strip():
        out.pop()
    if region.items and len(gone) == len(region.items):
        k = next(k for k, x in enumerate(out) if x.startswith(lines[region.key_line]))
        out[k] = re.sub(r":.*$", ": []", out[k])
    return out


_PAD = "\0"  # stands in for the other lines of a replaced item, so line numbers hold


def _rewrite(
    lines: list[str], region: _ListRegion, gone: set[int], replace: Mapping[int, list[str]]
) -> list[str]:
    """The lines with the items starting at `replace` replaced by new lines and those at
    `gone` dropped (with their emptied section headers, see _drop)."""
    ends = dict(region.items)
    padded = list(lines)
    for start, new in replace.items():
        padded[start] = "\n".join(new)
        for k in range(start + 1, ends[start]):
            padded[k] = _PAD
    out = _drop(padded, region, gone) if gone else padded
    return [part for x in out if x != _PAD for part in x.split("\n")]


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value)
    if _BARE.match(text) and text not in _YAML_WORDS:
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _flow(value: Any) -> str:
    """A part's flow mapping, in templates.yaml's style."""
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k}: {_flow(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_flow(v) for v in value) + "]"
    return _scalar(value)


def _text(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


# --- the files -------------------------------------------------------------------------------


def _prune_list(text: str, key: str, removed: set[str]) -> str:
    lines = text.splitlines()
    region = _region(lines, key)
    if region is None:
        return text
    gone = {s for s, e in region.items if _load_item(lines[s:e]).get("show") in removed}
    return _text(_drop(lines, region, gone)) if gone else text


def _prune_overrides(
    text: str, removed: set[str], ids: Mapping[str, tuple[str | None, int | None]], notes: list[str]
) -> str:
    lines = text.splitlines()
    region = _region(lines, "shows")
    if region is None:
        lines += ["shows:"]
        region = _ListRegion(len(lines) - 1, len(lines), [], 2)
    gone: set[int] = set()
    edits: dict[int, list[str]] = {}  # item start -> its new lines
    seen: set[str] = set()
    for s, e in region.items:
        item = _load_item(lines[s:e])
        show = str(item.get("show"))
        seen.add(show)
        if show in removed:
            gone.add(s)
            continue
        ref, year = ids.get(show, (None, None))
        add: list[tuple[str, str]] = []
        if ref:
            if item.get("tmdb_id") is None:
                add.append(("tmdb_id", _scalar(ref)))
            elif item["tmdb_id"] != ref:
                notes.append(
                    f"kept overrides.yaml's tmdb_id {item['tmdb_id']} for {show}"
                    f" (the list says {ref})"
                )
        if year and item.get("year") is None:
            add.append(("year", str(year)))
        if not add:
            continue
        span = lines[s:e]
        last = span[-1].rstrip()
        if len(span) == 1 and last.endswith("}"):
            body = last[:-1].rstrip()
            span = [body + ", " + ", ".join(f"{k}: {v}" for k, v in add) + " }"]
        else:
            key_indent = " " * (region.item_indent + 2)
            span = [*span, *(f"{key_indent}{k}: {v}" for k, v in add)]
        edits[s] = span
    new = [
        show for show in sorted(ids) if show not in seen and show not in removed and any(ids[show])
    ]
    lines = _rewrite(lines, region, gone, edits)
    if new:
        indent = " " * region.item_indent
        lines = [re.sub(r"^shows:\s*\[\]\s*$", "shows:", x) for x in lines]
        if not any(x.strip() == IDS_HEADER for x in lines):
            lines += ["", indent + IDS_HEADER]
        for show in new:
            ref, year = ids[show]
            fields = [f"show: {_scalar(show)}"]
            fields += [f"tmdb_id: {_scalar(ref)}"] if ref else []
            fields += [f"year: {year}"] if year else []
            lines.append(f"{indent}- {{ {', '.join(fields)} }}")
    return _text(lines)


def _prune_templates(
    text: str, removed: set[str], catalog: Catalog
) -> tuple[str, list[str], list[str]]:
    """(new text, dropped template ids, notes). A show is alive in a template when the
    channel carries it at some point of the template's years."""
    lines = text.splitlines()
    region = _region(lines, "templates")
    if region is None:
        return text, [], []
    windows: dict[tuple[str, str], list[tuple[date, date]]] = defaultdict(list)
    blocks: dict[tuple[str, str], set[str]] = defaultdict(set)
    for c in catalog.carriage.carriage:
        windows[(c.channel, c.show)].append((c.from_, c.until or date.max))
        if c.block:
            blocks[(c.channel, c.block)].add(c.show)
    eras = {t.id: (t.from_, t.until or date.max) for t in catalog.templates.templates}
    gone: set[int] = set()
    replace: dict[int, list[str]] = {}  # template start -> its new lines
    dropped: list[str] = []
    notes: list[str] = []
    refilled: dict[tuple[str, str, str], list[str]] = defaultdict(list)  # -> weekdays
    for s, e in region.items:
        template = _load_item(lines[s:e])
        tid, channel = str(template["id"]), str(template["channel"])
        start, end = eras[tid]
        years = range(max(start.year, 1990), min(end.year, 2010) + 1)

        def carried_in(show: object, y: int, ch: str = channel, lo: date = start,
                       hi: date = end) -> bool:  # fmt: skip
            a, b = max(lo, date(y, 1, 1)), min(hi, date(y, 12, 31))
            return show not in removed and any(
                x <= b and a <= z for x, z in windows[(ch, str(show))]
            )

        def alive(show: object, ch: str = channel, lo: date = start, hi: date = end) -> bool:
            return show not in removed and any(
                a <= hi and lo <= b for a, b in windows[(ch, str(show))]
            )

        def block_alive(block: object, ch: str = channel) -> bool:
            return any(alive(x) for x in blocks[(ch, str(block))])

        parts_at = next((k for k in range(s, e) if lines[k].strip().startswith("parts:")), None)
        if parts_at is None:
            continue
        spans, _ = _spans(lines, parts_at + 1, e)
        parts = [_load_item(lines[a:b]) for a, b in spans]
        pools = [entry for p in parts for entry in p.get("pool") or []]
        remaining = list(dict.fromkeys(str(x["show"]) for x in pools if alive(x.get("show"))))
        lost = sorted({str(x["show"]) for x in pools if x.get("show") in removed})
        died = {  # blocks that carried something here before the prune, and nothing now
            str(p["block"]) for p in parts if p.get("block")
            and not block_alive(p["block"])
            and any(a <= end and start <= b for x in blocks[(channel, str(p["block"]))]
                    for a, b in windows[(channel, x)])
        }  # fmt: skip
        if not lost and not died:
            continue
        new_parts: list[dict[str, Any]] = []
        for p in parts:
            q = dict(p)
            had = p.get("pool") or []
            pool = [x for x in had if x.get("show") not in removed]
            if "pool" in q:
                q["pool"] = pool
            emptied = len(pool) < len(had) and not any(
                x.get("tag") or alive(x.get("show")) for x in pool
            )
            if emptied or (p.get("block") in died and not pool):
                if p.get("block") and block_alive(p["block"]):
                    q["pool"] = []  # everything the block still carries
                else:
                    q.pop("block", None)
                    q["pool"] = [{"show": x} for x in remaining]
                    refilled[(tid, str(q["start"]), "all")].append(str(q["weekday"])[:3])
            elif len(pool) < len(had) and pool and not any(x.get("tag") for x in pool):
                # Still something to air, but maybe not in every year any more.
                missing = [y for y in years if not any(carried_in(x.get("show"), y) for x in pool)]
                names = {str(x.get("show")) for x in pool}
                extra = [
                    x for x in remaining
                    if x not in names and any(carried_in(x, y) for y in missing)
                ]  # fmt: skip
                if extra:
                    q["pool"] = [*pool, *({"show": x} for x in extra)]
                    key = f"{_years(missing)}; {', '.join(extra)}"
                    refilled[(tid, str(q["start"]), key)].append(str(q["weekday"])[:3])
            if "pool" in q and not q["pool"] and "pool" not in p:
                q.pop("pool")
            new_parts.append(q)
        if not remaining and not any(p.get("block") and block_alive(p["block"]) for p in new_parts):
            gone.add(s)
            dropped.append(tid)
            continue
        if lost:
            notes.append(f"{tid}: {', '.join(lost)} left its pools")
        body = lines[s:e]
        for (a, b), old, q in sorted(
            zip(spans, parts, new_parts, strict=True), key=lambda x: -x[0][0]
        ):
            if q != old:
                body[a - s : b - s] = [" " * _indent(lines[a]) + "- " + _flow(q)]
        replace[s] = body
    for (tid, at, key), days in refilled.items():
        when = "/".join(days)
        if key == "all":
            notes.append(
                f"{tid} {when} {at}: nothing in its pool airs in these years any more;"
                " it plays the template's other shows"
            )
        else:
            notes.append(f"{tid} {when} {at}: nothing in its pool airs in {key} join it")
    if not gone and not replace:
        return text, dropped, notes
    return _text(_rewrite(lines, region, gone, replace)), dropped, notes


# --- the whole prune -------------------------------------------------------------------------


@dataclass
class Report:
    decisions: list[Decision]
    removed: list[Decision]
    dropped_templates: list[str]
    notes: list[str]
    empty_channels: list[str]
    not_carried: list[ListRow]  # kept rows no carried show matched: the builder places them
    errors: list[str]
    written: bool
    after: Catalog | None = None  # the pruned catalog, when it validates

    @property
    def kept(self) -> int:
        return sum(d.keep for d in self.decisions)


FILES = ("carriage", "synthetic", "overrides", "templates")


def plan(
    catalog_dir: Path,
    rows: list[ListRow],
    same: Mapping[str, str] | None = None,
    keep: Iterable[str] = (),
) -> tuple[dict[str, str], Report]:
    """The pruned file texts (only files that change) and the report; nothing is written."""
    catalog = load_catalog(catalog_dir)
    decisions = decide(catalog, rows, same, keep)
    removed = {d.show for d in decisions if not d.keep}
    ids = {d.show: (d.row.tmdb_id, d.row.year) for d in decisions if d.keep and d.row}
    texts = {n: (catalog_dir / f"{n}.yaml").read_text() for n in FILES
             if (catalog_dir / f"{n}.yaml").exists()}  # fmt: skip
    notes: list[str] = []
    new: dict[str, str] = {}
    if "carriage" in texts:
        new["carriage"] = _prune_list(texts["carriage"], "carriage", removed)
    if "synthetic" in texts:
        new["synthetic"] = _prune_list(texts["synthetic"], "synthetic", removed)
    new["overrides"] = _prune_overrides(texts.get("overrides", ""), removed, ids, notes)
    carried_after: dict[str, set[str]] = defaultdict(set)
    for c in catalog.carriage.carriage:
        if c.show not in removed:
            carried_after[c.channel].add(c.show)
    dropped: list[str] = []
    if "templates" in texts:
        new["templates"], dropped, template_notes = _prune_templates(
            texts["templates"], removed, catalog
        )
        notes += template_notes
    changed = {n: t for n, t in new.items() if t != texts.get(n)}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for p in catalog_dir.glob("*.yaml"):
            (root / p.name).write_text(p.read_text())
        for n, t in changed.items():
            (root / f"{n}.yaml").write_text(t)
        after: Catalog | None = None
        try:
            after = load_catalog(root)
            errors = validate(after)
        except CatalogError as e:
            errors = [str(e)]

    matched = {id(d.row) for d in decisions if d.row is not None}
    report = Report(
        decisions=decisions,
        removed=[d for d in decisions if not d.keep],
        dropped_templates=dropped,
        notes=notes,
        empty_channels=sorted(
            c.id for c in catalog.channels.channels if not carried_after.get(c.id)
        ),
        not_carried=[r for r in rows if r.kept and id(r) not in matched],
        errors=errors,
        written=False,
        after=None if errors else after,
    )
    return changed, report


def prune(
    catalog_dir: Path,
    rows: list[ListRow],
    same: Mapping[str, str] | None = None,
    keep: Iterable[str] = (),
    write: bool = True,
) -> Report:
    """Prune the catalog in place (unless `write` is False). Nothing is written when the
    result would not validate."""
    changed, report = plan(catalog_dir, rows, same, keep)
    if write and not report.errors:
        for n, t in changed.items():
            (catalog_dir / f"{n}.yaml").write_text(t)
        report.written = True
    return report


def coverage_gaps(catalog: Catalog) -> list[str]:
    """Template parts with a year (inside the template and 1990-2010) in which no show of its
    pool (or block) is carried on the channel: those hours are Off Air that year."""
    windows: dict[tuple[str, str], list[tuple[date, date]]] = defaultdict(list)
    blocks: dict[tuple[str, str], set[str]] = defaultdict(set)
    for c in catalog.carriage.carriage:
        windows[(c.channel, c.show)].append((c.from_, c.until or date(2010, 12, 31)))
        if c.block:
            blocks[(c.channel, c.block)].add(c.show)
    out: list[str] = []
    for t in catalog.templates.templates:
        first = max(t.from_.year, 1990)
        last = min((t.until or date(2010, 12, 31)).year, 2010)
        seen: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
        for p in t.parts:
            shows = tuple(e.show for e in p.pool if e.show) or tuple(
                sorted(blocks.get((t.channel, p.block or ""), set()))
            )
            seen[(p.start, shows)].append(p.weekday[:3])
        for (start, shows), days in seen.items():
            missing = [
                y
                for y in range(first, last + 1)
                if not any(a.year <= y <= b.year for s in shows for a, b in windows[(t.channel, s)])
            ]
            if missing:
                out.append(f"{t.id} {'/'.join(days)} {start}: nothing carried in {_years(missing)}")
    return out


def idle_years(catalog: Catalog) -> list[str]:
    """Channels with years (from launch, 1990-2010) in which they carry nothing at all."""
    windows: dict[str, list[tuple[date, date]]] = defaultdict(list)
    for c in catalog.carriage.carriage:
        windows[c.channel].append((c.from_, c.until or date(2010, 12, 31)))
    out = []
    for ch in catalog.channels.channels:
        first = max((ch.launch or date(1990, 1, 1)).year, 1990)
        missing = [
            y
            for y in range(first, 2011)
            if not any(a.year <= y <= b.year for a, b in windows[ch.id])
        ]
        if missing:
            out.append(f"{ch.id}: nothing carried in {_years(missing)}")
    return out


def _years(years: list[int]) -> str:
    runs: list[list[int]] = []
    for y in years:
        if runs and y == runs[-1][-1] + 1:
            runs[-1].append(y)
        else:
            runs.append([y])
    return ", ".join(str(r[0]) if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in runs)


def render(report: Report) -> str:
    lines = [
        f"kept {report.kept} carried titles, removed {len(report.removed)}"
        f" ({sum(d.how.startswith('remove row') for d in report.removed)} remove rows,"
        f" {sum(d.how == 'not in the list' for d in report.removed)} not in the list)",
        f"kept list titles no channel carries yet (for the channel builder):"
        f" {len(report.not_carried)}",
        "",
        "Fuzzy matches and judgment calls:",
    ]
    fuzzy = [d for d in report.decisions if d.fuzzy]
    lines += [
        f"  {d.show} ({d.title}): {'kept' if d.keep else 'removed'}, {d.how}"
        + (f" {d.note}" if d.note else "")
        for d in fuzzy
    ] or ["  none"]
    lines += ["", "Removed:"]
    lines += [f"  {d.show} ({d.title}): removed, {d.how}" for d in report.removed] or ["  none"]
    lines += ["", f"Templates dropped: {', '.join(report.dropped_templates) or 'none'}"]
    lines += [f"  {n}" for n in report.notes]
    lines += ["", f"Channels left with no carriage: {', '.join(report.empty_channels) or 'none'}"]
    if report.after is not None:
        lines += ["", "Channels with years they carry nothing (the builder can add shows):"]
        lines += [f"  {g}" for g in idle_years(report.after)] or ["  none"]
        gaps = coverage_gaps(report.after)
        lines += ["", "Template parts with nothing carried in some years (Off Air then):"]
        lines += [f"  {g}" for g in gaps] or ["  none"]
    lines += [""]
    if report.errors:
        lines += ["catalog would not validate:", *(f"  {e}" for e in report.errors)]
    else:
        lines += ["catalog ok"]
    lines += ["written" if report.written else "dry run: nothing written"]
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m retro_cable.channel_builder.lineup")
    p.add_argument("job", choices=["prune"])
    p.add_argument("--shows", type=Path, required=True, help="the show list CSV")
    p.add_argument("--catalog", type=Path, default=Path("catalog"))
    p.add_argument(
        "--same", action="append", default=[], metavar="SHOW=TITLE",
        help="judgment call: carried SHOW is the list's row TITLE",
    )  # fmt: skip
    p.add_argument(
        "--keep", action="append", default=[], metavar="SHOW",
        help="judgment call: keep carried SHOW (no row to take ids from)",
    )  # fmt: skip
    p.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    same: dict[str, str] = {}
    for item in args.same:
        show, sep, title = item.partition("=")
        if not sep or not show.strip() or not title.strip():
            print(f"--same takes SHOW=TITLE, not '{item}'", file=sys.stderr)
            return 2
        same[show.strip()] = title.strip()
    try:
        report = prune(args.catalog, read_list(args.shows), same, args.keep, write=not args.dry_run)
    except (CatalogError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1
    print(render(report), end="")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
