"""Brandon's show list: the lineup CSV (one row per show) read into typed rows.

Columns: action, review, category, title, kind, first_aired, after_2010, original_network,
on_nas, already_carried, tmdb_id, aka, notes (and anything else, ignored). Only rows whose
action is `keep` or `add` are kept. Ids are resolved against what already exists, so a show
keeps one id everywhere: a carried catalog title first, then a library (database) title, then
the slug `ingest scan` would give the folder.
"""

import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from retro_cable.ingest.plex import normalize_title

KEEP = frozenset({"keep", "add"})
Kind = Literal["show", "movie", "special"]
CATEGORIES = ("preschool", "kids", "family", "teen", "adult", "anime")


@dataclass(frozen=True)
class ShowRow:
    id: str
    title: str
    kind: Kind
    category: str  # one of CATEGORIES, or "unknown"
    first_aired: date | None  # Jan 1 when the list gives only a year
    after_2010: bool  # first aired after 2010, so it needs a pretend premiere to air
    network: str  # the original network, context only: the builder may put it anywhere
    on_nas: bool
    tmdb_id: str
    aka: tuple[str, ...] = ()

    @property
    def debut_year(self) -> int | None:
        return self.first_aired.year if self.first_aired else None


def slug(title: str) -> str:
    """The id `ingest scan` gives a show folder of this title."""
    return "-".join(normalize_title(title).split()) or "show"


def parse_first_aired(text: str) -> date | None:
    text = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return date(int(text[:4]), 1, 1)
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return date(int(text[:4]), int(text[5:]), 1)
    if re.fullmatch(r"\d{4}", text):
        return date(int(text), 1, 1)
    return None


def _flag(text: str) -> bool:
    return text.strip().lower() in ("yes", "y", "true", "1", "x")


def _kind(text: str) -> Kind:
    k = text.strip().lower()
    return "movie" if k == "movie" else "special" if k == "special" else "show"


def read_shows(path: Path, known: Mapping[str, str] | None = None) -> list[ShowRow]:
    """The kept rows of a lineup CSV. `known` maps normalized titles to existing ids (see
    known_ids); a title seen twice keeps its first row."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return rows_from(csv.DictReader(f), known or {})


def rows_from(records: Iterable[Mapping[str, str]], known: Mapping[str, str]) -> list[ShowRow]:
    rows: list[ShowRow] = []
    ids: set[str] = set()
    for r in records:
        if (r.get("action") or "").strip().lower() not in KEEP:
            continue
        title = (r.get("title") or "").strip()
        if not title:
            continue
        aka = tuple(a.strip() for a in (r.get("aka") or "").split(";") if a.strip())
        found = next(
            (known[k] for k in (normalize_title(t) for t in (title, *aka)) if k in known), None
        )
        show_id = found or slug(title)
        if show_id in ids:
            continue
        ids.add(show_id)
        first = parse_first_aired(r.get("first_aired") or "")
        category = (r.get("category") or "").strip().lower()
        rows.append(
            ShowRow(
                id=show_id,
                title=title,
                kind=_kind(r.get("kind") or ""),
                category=category if category in CATEGORIES else "unknown",
                first_aired=first,
                after_2010=_flag(r.get("after_2010") or "")
                or (first is not None and first.year > 2010),
                network=(r.get("original_network") or "").strip(),
                on_nas=_flag(r.get("on_nas") or ""),
                tmdb_id=(r.get("tmdb_id") or "").strip(),
                aka=aka,
            )
        )
    return rows


def known_ids(
    catalog_titles: Mapping[str, str], library_titles: Mapping[str, str]
) -> dict[str, str]:
    """Normalized title -> id: carried catalog titles win over library titles."""
    out = {normalize_title(t): i for i, t in library_titles.items()}
    out.update({normalize_title(t): i for i, t in catalog_titles.items()})
    return out
