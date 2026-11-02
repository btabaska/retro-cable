"""Effective air dates for shows that use a US debut or a synthetic premiere."""

from collections.abc import Sequence
from datetime import date, timedelta
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


SEASON_DAYS = 365  # an undated season N starts N-1 years after the bound
EPISODE_DAYS = 16  # and its episodes follow half a month apart


def synthesize_undated(
    keys: Sequence[tuple[int, int]], dates: Sequence[date | None], bound: date | None
) -> list[date | None]:
    """Fill missing dates late enough that no episode airs before it really did.

    `bound` is a date the show had certainly debuted by. Season s, episode e (both from 1)
    gets bound + (s-1) years + (e-1) half-months, raised to the latest date of any episode
    before it in air order. Real seasons run about weekly from the fall, so this lands on or
    after the real date for typical shows; specials (season 0 or episode 0) and seasons
    numbered by year stay undated.
    """
    out = list(dates)
    if bound is None:
        return out
    latest: date | None = None
    for i in sorted(range(len(keys)), key=lambda i: keys[i]):
        season, episode = keys[i]
        if out[i] is None and 1 <= season < 1900 and episode >= 1:
            guess = bound + timedelta(
                days=SEASON_DAYS * (season - 1) + EPISODE_DAYS * (episode - 1)
            )
            out[i] = max(guess, latest) if latest else guess
        current = out[i]
        if current is not None and (latest is None or current > latest):
            latest = current
    return out


def _debut_bound(catalog: "Catalog", show: str, first_year: int | None) -> date | None:
    """A date the show had certainly debuted by: its synthetic premiere or US debut, else Dec 31
    of its premiere year, else Dec 31 of the year it was first carried on a network channel
    (library channels carry shows long after their debut, so they say nothing)."""
    anchor = _anchor(catalog, show)
    if anchor is not None:
        return anchor
    if first_year is not None:
        return date(first_year, 12, 31)
    library = {c.id for c in catalog.channels.channels if c.library}
    starts = [
        c.from_ for c in catalog.carriage.carriage if c.show == show and c.channel not in library
    ]
    return date(min(starts).year, 12, 31) if starts else None


def effective_dates(
    catalog: "Catalog",
    show: str,
    episodes: Sequence[tuple[int, int, date | None]],
    first_year: int | None = None,
) -> list[date | None]:
    """Air dates the scheduler uses for (season, episode, original date) rows, in the same order.

    Per-episode dates from overrides.yaml replace the originals first; then a synthetic premiere
    or US debut shifts the whole show (see shift_air_dates). Episodes still without a date get a
    conservative one (see synthesize_undated); `first_year` is the show's premiere year.
    """
    fixes: dict[tuple[int, int], date] = {}
    for o in catalog.overrides.shows:
        if o.show == show:
            fixes = {episode_key(k): d for k, d in o.episodes.items()}
    dates = [fixes.get((s, e), d) for s, e, d in episodes]
    shifted = shift_air_dates(dates, _anchor(catalog, show))
    keys = [(s, e) for s, e, _ in episodes]
    return synthesize_undated(keys, shifted, _debut_bound(catalog, show, first_year))
