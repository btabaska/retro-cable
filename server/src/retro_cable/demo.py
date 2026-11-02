"""Seed a demo database so the whole app runs on fixture media: `make demo`.

Every show carried in catalog/carriage.yaml gets weekly episodes, 22 per season, from its first
carriage date (shows already on the air in 1990 get a back catalog from 1986). Every episode
points at one of the generic demo feeds in <media>/_demo/ (see tools/fixtures/generate.py
--demo) by runtime class, and every channel gets the demo bumper. Movies premiere on their
first carriage date on a non-library channel. Air dates are invented: this is for trying the
app, not for measuring the archive (that is `make inventory` on a real import).
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from retro_cable.catalog import load_catalog
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
                    (show, titles[show], start.isoformat(), seconds, path),
                )
                movies += 1
                continue
            db.execute("INSERT INTO shows (id, title) VALUES (?, ?)", (show, titles[show]))
            shows += 1
            if start <= date(1990, 1, 1):
                start = date(1986, 9, 14)
            for season in range(MAX_SEASONS):
                opener = start + timedelta(weeks=52 * season)
                if opener > last[show]:
                    break
                for n in range(EPISODES_PER_SEASON):
                    aired = opener + timedelta(weeks=n)
                    db.execute(
                        "INSERT INTO episodes (show_id, season, episode, title, air_date,"
                        " air_date_source, runtime_seconds, file_path)"
                        " VALUES (?, ?, ?, ?, ?, 'tmdb', ?, ?)",
                        (show, season + 1, n + 1, f"Episode {season * EPISODES_PER_SEASON + n + 1}",
                         aired.isoformat(), seconds, path),
                    )  # fmt: skip
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
