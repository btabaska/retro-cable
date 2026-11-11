"""Holiday dates, windows and tags (Phase 7).

A holiday's **window** is the `window_days` up to and including the holiday (Halloween with
7 days: Oct 25-31). Inside it, a channel whose mode is `on` prefers its shows' tagged episodes
and both `on` and `specials_only` channels get holiday movies and specials in prime time.

A holiday's **season** is its window or, if longer, the holiday's month up to the holiday
(Halloween: Oct 1-31). Seasonal items (holiday movies, and tagged episodes of shows marked
`seasonal` in overrides.yaml, like the Peanuts specials) only air inside one of their seasons,
so A Charlie Brown Christmas never turns up in July.

Tags come from, in order of precedence:
1. `overrides.yaml` `holiday_tags` for that episode (replaces everything below, so `[]` untags);
2. `holidays.yaml` `tagged` items, matched by title;
3. a keyword pass over the episode title (or the movie title), per holiday;
4. for episodes, the same pass over the description (Plex's or TMDB's), with stricter words
   where a name could be mistaken for the holiday (`DESCRIPTION_KEYWORDS`). Movies never take
   tags from their description: a tagged movie airs only in season, so a passing mention
   ("on the first day of Christmas break") would take it off the air most of the year.
Everything here is a pure function of the catalog and the program's titles.
"""

import calendar
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from retro_cable.catalog.air_dates import episode_key
from retro_cable.catalog.load import Catalog
from retro_cable.catalog.models import Holiday, HolidayMode

WEEKDAY_INDEX = {
    name: i
    for i, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    )
}

# Title keywords per holiday id, matched on word boundaries, case- and accent-insensitively.
# Every holiday also matches its own name ("Valentine's Day"). Keep these specific: "santa"
# alone would tag Santa Barbara, and "turkey" the country.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "halloween": (
        "halloween",
        "halloweentown",
        "treehouse of horror",
        "trick or treat",
        "trick-or-treat",
        "great pumpkin",
        "jack-o-lantern",
    ),
    "thanksgiving": ("thanksgiving",),
    "christmas": (
        "christmas",
        "xmas",
        "santa claus",
        "rudolph",
        "frosty the snowman",
        "the grinch",
        "nutcracker",
        "jingle bell",
        "yuletide",
    ),
    "new-years-eve": ("new year's eve", "new years eve"),
    "valentines": ("valentine", "valentine's"),
    "st-patricks": (
        "st. patrick",
        "saint patrick",
        "st patrick",
        "leprechaun",
        "luck of the irish",
    ),
    "independence-day": ("fourth of july", "4th of july", "independence day"),
}

# In descriptions, where a word is more often a name or a character than the holiday:
# "Vicki Valentine", a leprechaun in Imaginationland. Every holiday's own name still counts.
DESCRIPTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "valentines": ("valentine card", "valentine's card", "valentines day"),
    "st-patricks": ("st. patrick's day", "saint patrick's day", "st patrick's day"),
}

STRENGTH_BOOST = {"low": 1.5, "medium": 2.0, "strong": 3.0}


def normalize(text: str) -> str:
    curly = text.replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    folded = unicodedata.normalize("NFKD", curly).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", folded.lower()).strip()


Patterns = tuple[tuple[str, str], ...]  # (holiday id, regex source), hashable


def _pattern(words: Iterable[str]) -> str:
    alternatives = "|".join(re.escape(normalize(w)) for w in sorted(words))
    return rf"(?<![a-z0-9])(?:{alternatives})"


@lru_cache(maxsize=64)
def _compiled(patterns: Patterns) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((hid, re.compile(rx)) for hid, rx in patterns)


@lru_cache(maxsize=1 << 16)
def _matches(patterns: Patterns, text: str) -> frozenset[str]:
    """The holidays whose words `text` names. Remembered: the scheduler builds a Tagger on
    every rebuild, and scanning thousands of descriptions each time cost half a second."""
    folded = normalize(text)
    return frozenset(hid for hid, rx in _compiled(patterns) if rx.search(folded))


def holiday_date(holiday: Holiday, year: int) -> date:
    rule = holiday.rule
    if rule.type == "fixed":
        last = calendar.monthrange(year, rule.month)[1]
        return date(year, rule.month, min(rule.day, last))
    target = WEEKDAY_INDEX[rule.weekday]
    if rule.n == -1:
        last_day = date(year, rule.month, calendar.monthrange(year, rule.month)[1])
        return last_day - timedelta(days=(last_day.weekday() - target) % 7)
    first = date(year, rule.month, 1)
    day = first + timedelta(days=(target - first.weekday()) % 7 + 7 * (rule.n - 1))
    if day.month != rule.month:  # a fifth weekday that this month doesn't have
        day -= timedelta(days=7)
    return day


def window(holiday: Holiday, year: int) -> tuple[date, date]:
    end = holiday_date(holiday, year)
    return end - timedelta(days=holiday.window_days - 1), end


def season(holiday: Holiday, year: int) -> tuple[date, date]:
    start, end = window(holiday, year)
    return min(start, end.replace(day=1)), end


def _within(span: tuple[date, date], day: date) -> bool:
    return span[0] <= day <= span[1]


def in_window(holiday: Holiday, day: date) -> bool:
    return _within(window(holiday, day.year), day)


def in_season(holiday: Holiday, day: date) -> bool:
    return _within(season(holiday, day.year), day)


def mode_for(holiday: Holiday, channel_id: str) -> HolidayMode:
    return holiday.channels.get(channel_id, holiday.default_mode)


@dataclass(frozen=True)
class Calendar:
    """The holidays in force on one date."""

    window: tuple[Holiday, ...]  # holidays whose window contains the date
    season: frozenset[str]  # ids of holidays whose season contains the date


class Tagger:
    """Holiday tags for programs, from overrides, holidays.yaml tagged items and keywords."""

    def __init__(self, catalog: Catalog) -> None:
        self.holidays = {h.id: h for h in catalog.holidays.holidays}
        patterns: list[tuple[str, str]] = []
        described: list[tuple[str, str]] = []
        for h in catalog.holidays.holidays:
            words = {normalize(h.name), *KEYWORDS.get(h.id, ())}
            patterns.append((h.id, _pattern(words)))
            strict = DESCRIPTION_KEYWORDS.get(h.id)
            described.append((h.id, _pattern({normalize(h.name), *strict} if strict else words)))
        self._patterns: Patterns = tuple(patterns)
        self._described: Patterns = tuple(described)
        self._tagged: dict[tuple[str, str], set[str]] = {}
        for h in catalog.holidays.holidays:
            for item in h.tagged:
                kind = "episode" if item.kind == "episode" else "movie"
                self._tagged.setdefault((kind, normalize(item.title)), set()).add(h.id)
        self._specials = {
            normalize(item.title)
            for h in catalog.holidays.holidays
            for item in h.tagged
            if item.kind == "special"
        }
        self._overrides: dict[tuple[str, int, int], frozenset[str]] = {}
        self.seasonal_shows: frozenset[str] = frozenset(
            o.show for o in catalog.overrides.shows if o.seasonal
        )
        for o in catalog.overrides.shows:
            for key, tags in o.holiday_tags.items():
                season_no, episode_no = episode_key(key)
                self._overrides[(o.show, season_no, episode_no)] = frozenset(tags)
        self._calendar: dict[date, Calendar] = {}

    def keyword_tags(self, text: str) -> frozenset[str]:
        return _matches(self._patterns, text)

    def description_tags(self, text: str) -> frozenset[str]:
        return _matches(self._described, text)

    def tags(
        self,
        show: str,
        kind: str,
        title: str,
        episode_title: str = "",
        season: int | None = None,
        episode: int | None = None,
        summary: str = "",
    ) -> frozenset[str]:
        if kind != "movie" and season is not None and episode is not None:
            fixed = self._overrides.get((show, season, episode))
            if fixed is not None:
                return fixed
        text = title if kind == "movie" else episode_title
        lookup = "movie" if kind == "movie" else "episode"
        listed = self._tagged.get((lookup, normalize(text)), set())
        found = frozenset(listed) | self.keyword_tags(text)
        if kind != "movie" and summary:
            found |= self.description_tags(summary)
        return found

    def is_seasonal(self, show: str, kind: str, title: str, tags: Iterable[str]) -> bool:
        """Seasonal items air only in season: holiday movies and specials, and every episode
        of a show marked `seasonal` in overrides.yaml. An untagged episode of a seasonal show
        has no season, so it never airs (the inventory lists it as an untagged special)."""
        if show in self.seasonal_shows:
            return True
        if not tags:
            return False
        return kind == "movie" or normalize(title) in self._specials

    def on(self, day: date) -> Calendar:
        found = self._calendar.get(day)
        if found is None:
            found = Calendar(
                window=tuple(h for h in self.holidays.values() if in_window(h, day)),
                season=frozenset(hid for hid, h in self.holidays.items() if in_season(h, day)),
            )
            self._calendar[day] = found
        return found
