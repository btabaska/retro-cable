"""Effective air dates for shows that use a US debut or a synthetic premiere."""

from collections.abc import Sequence
from datetime import date


def shift_air_dates(original: Sequence[date | None], anchor: date | None) -> list[date | None]:
    """Move a show's dates so its earliest episode airs on `anchor`, keeping the spacing.

    `original` is in air order; missing dates stay missing. No anchor returns the dates unchanged.
    """
    known = [d for d in original if d is not None]
    if anchor is None or not known:
        return list(original)
    delta = anchor - min(known)
    return [d + delta if d is not None else None for d in original]
