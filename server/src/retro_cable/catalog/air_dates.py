"""Effective air dates for shows that use a US debut or a synthetic premiere."""

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from retro_cable.catalog.load import Catalog


def shift_air_dates(original: Sequence[date | None], anchor: date | None) -> list[date | None]:
    """Move a show's dates so its earliest episode airs on `anchor`, keeping the spacing.

    `original` is in air order; missing dates stay missing. No anchor returns the dates unchanged.
    """
    known = [d for d in original if d is not None]
    if anchor is None or not known:
        return list(original)
    delta = anchor - min(known)
    return [d + delta if d is not None else None for d in original]


def episode_key(key: str) -> tuple[int, int]:
    """ "S1E2" or "S01E02" -> (1, 2)."""
    season, _, episode = key[1:].partition("E")
    return int(season), int(episode)


def _anchor(catalog: "Catalog", show: str) -> date | None:
    for s in catalog.synthetic.synthetic:
        if s.show == show and s.premiere:
            return s.premiere
    for o in catalog.overrides.shows:
        if o.show == show:
            return o.us_debut
    return None


def is_library_show(catalog: "Catalog", show: str) -> bool:
    """Library shows have every episode eligible whenever their channel is on."""
    return any(s.show == show and s.library for s in catalog.synthetic.synthetic)


def effective_dates(
    catalog: "Catalog", show: str, episodes: Sequence[tuple[int, int, date | None]]
) -> list[date | None]:
    """Air dates the scheduler uses for (season, episode, original date) rows, in the same order.

    Per-episode dates from overrides.yaml replace the originals first; then a synthetic premiere
    or US debut shifts the whole show (see shift_air_dates).
    """
    fixes: dict[tuple[int, int], date] = {}
    for o in catalog.overrides.shows:
        if o.show == show:
            fixes = {episode_key(k): d for k, d in o.episodes.items()}
    dates = [fixes.get((s, e), d) for s, e, d in episodes]
    return shift_air_dates(dates, _anchor(catalog, show))
