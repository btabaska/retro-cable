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
3. a keyword pass over the episode title (or the movie title), per holiday.
Everything here is a pure function of the catalog and the program's titles.
"""

import calendar
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

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

STRENGTH_BOOST = {"low": 1.5, "medium": 2.0, "strong": 3.0}


def normalize(text: str) -> str:
    curly = text.replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    folded = unicodedata.normalize("NFKD", curly).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", folded.lower()).strip()


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
        self._patterns: list[tuple[str, re.Pattern[str]]] = []
        for h in catalog.holidays.holidays:
            words = {normalize(h.name), *KEYWORDS.get(h.id, ())}
            alternatives = "|".join(re.escape(normalize(w)) for w in sorted(words))
            self._patterns.append((h.id, re.compile(rf"(?<![a-z0-9])(?:{alternatives})")))
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
        folded = normalize(text)
        return frozenset(hid for hid, rx in self._patterns if rx.search(folded))

    def tags(
        self,
        show: str,
        kind: str,
        title: str,
        episode_title: str = "",
        season: int | None = None,
        episode: int | None = None,
    ) -> frozenset[str]:
        if kind != "movie" and season is not None and episode is not None:
            fixed = self._overrides.get((show, season, episode))
            if fixed is not None:
                return fixed
        text = title if kind == "movie" else episode_title
        lookup = "movie" if kind == "movie" else "episode"
        listed = self._tagged.get((lookup, normalize(text)), set())
        return frozenset(listed) | self.keyword_tags(text)

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
