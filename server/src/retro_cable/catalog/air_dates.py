"""Effective air dates: the imported dates with overrides, a US debut, or a channel's synthetic
("pretend") premiere applied. A synthetic premiere belongs to one (show, channel) pair, so the
dates depend on the channel; every other channel keeps the real or US debut dates."""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import TYPE_CHECKING

from retro_cable.catalog.models import CUSTOM_PREFIX, Synthetic

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
    for o in catalog.overrides.shows:
        if o.show == show:
            return o.us_debut
    return None


def premiere_for(catalog: "Catalog", show: str, channel: str | None) -> Synthetic | None:
    """The synthetic premiere of `show` on `channel`, if one is set. The last entry wins, so a
    premiere set in /admin (merged after the YAML) replaces one in synthetic.yaml."""
    if channel is None:
        return None
    found = None
    for s in catalog.synthetic.synthetic:
        if s.show == show and s.channel == channel:
            found = s
    return found


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
    """A date the show had certainly debuted by: its US debut, else Dec 31
    of its premiere year, else Dec 31 of the year it was first carried on a network channel
    (library channels carry shows long after their debut, so they say nothing; nor do custom
    channels made in /admin, whose carriage is Brandon's choice, not broadcast history)."""
    anchor = _anchor(catalog, show)
    if anchor is not None:
        return anchor
    if first_year is not None:
        return date(first_year, 12, 31)
    ignored = {
        c.id for c in catalog.channels.channels if c.library or c.id.startswith(CUSTOM_PREFIX)
    }
    starts = [
        c.from_ for c in catalog.carriage.carriage if c.show == show and c.channel not in ignored
    ]
    return date(min(starts).year, 12, 31) if starts else None


PREMIERE_CADENCE = timedelta(weeks=1)  # undated episodes after a synthetic premiere


def premiere_dates(
    keys: Sequence[tuple[int, int]], dates: Sequence[date | None], premiere: date
) -> list[date | None]:
    """Dates on a channel with a synthetic premiere.

    The earliest dated episode moves to `premiere` and every other dated episode keeps its
    spacing from it (shift_air_dates). Each undated episode airs one week after the episode
    before it in (season, episode) order, or on the premiere if none comes before it, so a
    show with no dates at all runs weekly from the premiere. Specials (season 0 or episode 0)
    stay undated, as everywhere.
    """
    out = shift_air_dates(dates, premiere)
    previous: date | None = None
    for i in sorted(range(len(keys)), key=lambda i: keys[i]):
        season, episode = keys[i]
        current = out[i]
        if current is None and season >= 1 and episode >= 1:
            current = previous + PREMIERE_CADENCE if previous else premiere
            out[i] = current
        if current is not None and (previous is None or current > previous):
            previous = current
    return out


def effective_dates(
    catalog: "Catalog",
    show: str,
    episodes: Sequence[tuple[int, int, date | None]],
    first_year: int | None = None,
    channel: str | None = None,
) -> list[date | None]:
    """Air dates the scheduler uses for (season, episode, original date) rows, in the same order,
    on `channel` (None: a channel with no synthetic premiere for this show).

    Per-episode dates from overrides.yaml replace the originals first. Then the channel's
    synthetic premiere, if it has one for this show, shifts the whole show (premiere_dates);
    otherwise a US debut does (shift_air_dates), and episodes still without a date get a
    conservative one (synthesize_undated); `first_year` is the show's premiere year.
    """
    fixes: dict[tuple[int, int], date] = {}
    for o in catalog.overrides.shows:
        if o.show == show:
            fixes = {episode_key(k): d for k, d in o.episodes.items()}
    dates = [fixes.get((s, e), d) for s, e, d in episodes]
    keys = [(s, e) for s, e, _ in episodes]
    synthetic = premiere_for(catalog, show, channel)
    if synthetic is not None:
        return premiere_dates(keys, dates, synthetic.premiere)
    shifted = shift_air_dates(dates, _anchor(catalog, show))
    return synthesize_undated(keys, shifted, _debut_bound(catalog, show, first_year))
