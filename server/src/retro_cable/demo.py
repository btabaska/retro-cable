"""Seed a demo database so the whole app runs on fixture media: `make demo`.

Every show carried in catalog/carriage.yaml gets weekly episodes, 22 per season, from its first
carriage date (shows already on the air in 1990 get a back catalog from 1986). Every episode
points at one of the generic demo feeds in <media>/_demo/ (see tools/fixtures/generate.py
--demo) by runtime class, and every channel gets the demo bumper. Movies premiere on their
first carriage date on a non-library channel, except the holiday DCOMs, which keep their real
premieres. An episode that airs in the week up to a holiday is titled for it ("Episode 57:
Halloween"), so the holiday tagger finds it, and the Peanuts specials get their real titles.
Air dates are invented: this is for trying the app, not for measuring the archive (that is
`make inventory` on a real import).
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from retro_cable.catalog import Catalog, load_catalog
from retro_cable.catalog.holidays import holiday_date
from retro_cable.db import connect, migrate, transaction

EPISODES_PER_SEASON = 22
MAX_SEASONS = 12
LAST_DAY = date(2010, 12, 31)

QUARTER_HOUR = {
    "aqua-teen-hunger-force", "harvey-birdman-attorney-at-law", "metalocalypse", "robot-chicken",
    "sealab-2021", "space-ghost-coast-to-coast", "squidbillies", "the-brak-show",
    "tim-and-eric-awesome-show-great-job", "looney-tunes", "tom-and-jerry", "the-pink-panther",
    "what-a-cartoon", "i-am-weasel",
}  # fmt: skip
HOUR = {
    "24", "americas-most-wanted", "american-idol", "antiques-roadshow", "beverly-hills-90210",
    "bones", "csi-crime-scene-investigation", "diagnosis-murder", "dr-quinn-medicine-woman", "er",
    "freaks-and-geeks", "heroes", "house", "late-night-with-conan-obrien",
    "late-show-with-david-letterman", "law-and-order", "mystery", "mystery-science-theater-3000",
    "nature", "northern-exposure", "nova", "quantum-leap", "saturday-night-live", "survivor",
    "the-amazing-race", "the-o-c", "the-x-files", "touched-by-an-angel", "unsolved-mysteries",
    "walker-texas-ranger",
}  # fmt: skip
SPECIALS = {"rudolph-the-red-nosed-reindeer", "frosty-the-snowman", "peanuts-holiday-specials"}
# The shortest feed each runtime class accepts. Episodes take their feed's length, so short
# feeds make schedules no real channel had (and guides of tens of thousands of slots).
MIN_FEED_SECONDS = {
    "quarter-hour": 10 * 60, "half-hour": 20 * 60, "hour": 40 * 60, "movie": 80 * 60
}  # fmt: skip


# Real premieres for the holiday DCOMs, so Halloween in the demo looks like Halloween did.
PREMIERES = {
    "under-wraps": date(1997, 10, 25),
    "halloweentown": date(1998, 10, 17),
    "the-luck-of-the-irish": date(2001, 3, 9),
    "halloweentown-ii-kalabars-revenge": date(2001, 10, 12),
    "twitches": date(2005, 10, 14),
}
# Shows whose demo episodes are a fixed list of (title, air date) instead of weekly seasons.
FIXED_EPISODES = {
    "peanuts-holiday-specials": [
        ("A Charlie Brown Christmas", date(1965, 12, 9)),
        ("It's the Great Pumpkin, Charlie Brown", date(1966, 10, 27)),
        ("A Charlie Brown Thanksgiving", date(1973, 11, 20)),
        ("Be My Valentine, Charlie Brown", date(1975, 1, 28)),
    ]
}
HOLIDAY_WEEK = 7


def _episode_title(catalog: Catalog, number: int, aired: date) -> str:
    for h in catalog.holidays.holidays:
        day = holiday_date(h, aired.year)
        if day - timedelta(days=HOLIDAY_WEEK - 1) <= aired <= day:
            return f"Episode {number}: {h.name}"
    return f"Episode {number}"


def _feed(show: str, kind: str) -> str:
    if kind == "movie":
        return "half-hour" if show in SPECIALS else "movie"
    if show in QUARTER_HOUR:
        return "quarter-hour"
    return "hour" if show in HOUR else "half-hour"


def seed_demo(db_path: Path, catalog_dir: Path, media_dir: Path) -> dict[str, int]:
    catalog = load_catalog(catalog_dir)
    manifest = json.loads((media_dir / "_demo" / "manifest.json").read_text())
    feeds = {c["feed"]: (c["path"], int(c["seconds"])) for c in manifest["clips"]}
    short = [f"{k} ({feeds[k][1]} s)" for k, low in MIN_FEED_SECONDS.items() if feeds[k][1] < low]
    if short:
        listed = ", ".join(short)
        raise ValueError(f"demo feeds are too short to schedule: {listed}; run make demo-db")
    library_channels = {c.id for c in catalog.channels.channels if c.library}

    first: dict[tuple[str, str], date] = {}
    last: dict[str, date] = defaultdict(lambda: date(1990, 1, 1))
    titles: dict[str, str] = {}
    for c in catalog.carriage.carriage:
        key = (c.show, c.kind)
        titles[c.show] = c.title
        last[c.show] = max(last[c.show], c.until or LAST_DAY)
        if c.channel in library_channels and key in first:
            continue
        if key not in first or (c.channel not in library_channels and c.from_ < first[key]):
            first[key] = c.from_

    db = connect(db_path)
    migrate(db)
    shows = movies = episodes = 0
    with transaction(db):
        for table in ("episodes", "shows", "movies", "bumpers"):
            db.execute(f"DELETE FROM {table}")
        for (show, kind), start in sorted(first.items()):
            path, seconds = feeds[_feed(show, kind)]
            if kind == "movie":
                db.execute(
                    "INSERT INTO movies (id, title, premiere_date, runtime_seconds, file_path)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (show, titles[show], PREMIERES.get(show, start).isoformat(), seconds, path),
                )
                movies += 1
                continue
            db.execute("INSERT INTO shows (id, title) VALUES (?, ?)", (show, titles[show]))
            shows += 1
            if show in FIXED_EPISODES:
                for n, (title, aired) in enumerate(FIXED_EPISODES[show]):
                    db.execute(
                        "INSERT INTO episodes (show_id, season, episode, title, air_date,"
                        " air_date_source, runtime_seconds, file_path)"
                        " VALUES (?, 1, ?, ?, ?, 'tmdb', ?, ?)",
                        (show, n + 1, title, aired.isoformat(), seconds, path),
                    )
                    episodes += 1
                continue
            if start <= date(1990, 1, 1):
                start = date(1986, 9, 14)
            for season in range(MAX_SEASONS):
                opener = start + timedelta(weeks=52 * season)
                if opener > last[show]:
                    break
                for n in range(EPISODES_PER_SEASON):
                    aired = opener + timedelta(weeks=n)
                    title = _episode_title(catalog, season * EPISODES_PER_SEASON + n + 1, aired)
                    db.execute(
                        "INSERT INTO episodes (show_id, season, episode, title, air_date,"
                        " air_date_source, runtime_seconds, file_path)"
                        " VALUES (?, ?, ?, ?, ?, 'tmdb', ?, ?)",
                        (show, season + 1, n + 1, title, aired.isoformat(), seconds, path),
                    )
                    episodes += 1
        bumper_path, bumper_seconds = feeds["bumper"]
        for channel in catalog.channels.channels:
            db.execute(
                "INSERT INTO bumpers (channel_id, file_path, runtime_seconds) VALUES (?, ?, ?)",
                (channel.id, bumper_path, bumper_seconds),
            )
    db.close()
    return {"shows": shows, "movies": movies, "episodes": episodes}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m retro_cable.demo", description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("catalog"))
    parser.add_argument("--media", type=Path, required=True)
    args = parser.parse_args(argv[1:])
    stats = seed_demo(args.db, args.catalog, args.media)
    print(f"demo database {args.db}: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
