"""Multi-part stories (Brandon, Nov 15 2026): their parts air together, as one block."""

import itertools
from dataclasses import replace
from datetime import date, timedelta

import pytest

from retro_cable.catalog import Catalog
from retro_cable.scheduler import Library, Program, Scheduler, Slot
from retro_cable.scheduler.multipart import multipart_blocks, part_marker
from tests.scheduler.conftest import HALF, make_library, weekly


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Best of Both Worlds (1)", ("the best of both worlds", 1)),
        ("The Best of Both Worlds (2)", ("the best of both worlds", 2)),
        ("Who Shot Mr. Burns? Part One", ("who shot mr burns", 1)),
        ("Who Shot Mr. Burns? (Part Two)", ("who shot mr burns", 2)),
        ("The Return, Part II", ("the return", 2)),
        ("Escape - Pt. 3", ("escape", 3)),
        ("Wedding Bell Blues: Part 1", ("wedding bell blues", 1)),
        ("Finale (2 of 3)", ("finale", 2)),
        ("Into The Hornets Nest Part 1 of 2", ("into the hornets nest", 1)),
        ("Ron & Tammy Pt 2", ("ron tammy", 2)),
        ("Crime And Punishment Pt1", ("crime and punishment", 1)),
        # Both parts in one file: a whole episode, not a part.
        ("The Cadillac Part 1 and The Cadillac Part 2", None),
        ("+E02 London Pt 1 + Pt 2", None),
        ("The Boiling Rock Part 1 & Chapter Fifteen The Boiling Rock Part 2", None),
        ("The More You Moe (1) (2)", None),
        # A marker before a subtitle (Gravity Falls).
        ("Weirdmageddon (2): Escape from Reality", ("weirdmageddon", 2)),
        ("Pilot", None),
        ("Episode 12", None),
        ("Part Time Pal", None),  # "Part" is a word of the title, not a marker
        ("Catch-22", None),
        ("", None),
    ],
)
def test_part_marker_reads_the_part_number_off_the_title(
    title: str, expected: tuple[str, int] | None
) -> None:
    assert part_marker(title) == expected


def episode(n: int, title: str, show: str = "show") -> Program:
    return Program(
        show=show,
        kind="episode",
        title="Show",
        season=1,
        episode=n,
        episode_title=title,
        air_date=date(1995, 1, 1) + timedelta(weeks=n),
        seconds=HALF,
        path=f"{show}/{n}.mp4",
    )


def test_multipart_blocks_groups_consecutive_numbered_parts() -> None:
    eps = [
        episode(1, "Pilot"),
        episode(2, "The Big One (1)"),
        episode(3, "The Big One (2)"),
        episode(4, "The Big One (3)"),
        episode(5, "Ordinary"),
        episode(6, "Cliffhanger, Part 1"),
        episode(7, "Cliffhanger, Part 2"),
    ]
    blocks = multipart_blocks(eps)
    story = (("show", 1, 2), ("show", 1, 3), ("show", 1, 4))
    assert blocks[("show", 1, 2)] == story
    assert blocks[("show", 1, 3)] == story
    assert blocks[("show", 1, 4)] == story
    assert blocks[("show", 1, 6)] == (("show", 1, 6), ("show", 1, 7))
    assert ("show", 1, 1) not in blocks  # not part of a story: airs on its own
    assert ("show", 1, 5) not in blocks


def test_a_part_without_its_first_part_or_a_lone_first_part_is_held_back() -> None:
    """Part 2 on disk without Part 1 (not downloaded yet) would air as a single part, and so
    would a Part 1 whose Part 2 isn't on disk: both are held back (an empty block)."""
    eps = [
        episode(1, "Lost Half (2)"),
        episode(2, "Ordinary"),
        episode(3, "Waiting (1)"),
        episode(4, "Something Else"),
    ]
    blocks = multipart_blocks(eps)
    assert blocks[("show", 1, 1)] == ()
    assert blocks[("show", 1, 3)] == ()
    assert ("show", 1, 2) not in blocks
    assert ("show", 1, 4) not in blocks


def keys(*episodes: int) -> tuple[tuple[str, int, int], ...]:
    return tuple(("show", 1, n) for n in episodes)


@pytest.mark.parametrize(
    ("titles", "story"),
    [
        # Consecutive parts titled differently (Yu-Gi-Oh!, Roseanne, Adventure Time's Stakes).
        (["Into The Hornets Nest Part 1 of 2", "The Ultimate Great Moth Part 2 of 2"], (1, 2)),
        (["All Of Me Pt. 1", "To Tell The Truth Pt. 2"], (1, 2)),
        (["Marceline (1)", "Everything Stays (2)", "Vamps About (3)"], (1, 2, 3)),
        # Part 1 without a marker (The Simpsons' Warrin Priests, Avatar's Siege of the North).
        (["Warrin Priests", "Warrin Priests Part 2"], (1, 2)),
        (["The Siege of the North 1", "The Siege of the North Part 2"], (1, 2)),
        # A Part 1 followed by an untitled episode of the same season (Batman's Two-Face).
        (["Two-Face Part I", ""], (1, 2)),
        # The same story with an episode between its parts (Animaniacs segments).
        (["Season Three and WB Part 1", "How To Friendship", "Season Three and WB Part 2"],
         (1, 3)),
    ],
)  # fmt: skip
def test_multipart_blocks_finds_stories_however_their_parts_are_titled(
    titles: list[str], story: tuple[int, ...]
) -> None:
    eps = [episode(1 + i, t) for i, t in enumerate(titles)]
    blocks = multipart_blocks(eps)
    for n in story:
        assert blocks[("show", 1, n)] == keys(*story)
    for n in range(1, len(titles) + 1):
        if n not in story:
            assert ("show", 1, n) not in blocks


def test_a_two_in_one_episode_airs_like_any_other() -> None:
    eps = [episode(1, "Opener"), episode(2, "The Finale Part 1 and The Finale Part 2")]
    assert multipart_blocks(eps) == {}


def test_a_double_length_part_one_holds_both_parts() -> None:
    """A two-parter in one file (S02E01-E02) is named for its first episode, "Twilight (1)"
    (Justice League, Avatar, Nov 15 2026): a lone Part 1 at least 1.6x the show's usual length
    is the whole story and airs like any episode."""
    eps = [
        episode(1, "Ordinary"),
        replace(episode(2, "Twilight (1)"), seconds=HALF * 2),
        episode(4, "Another"),
        episode(5, "Waiting (1)"),  # a usual-length Part 1 whose Part 2 isn't on disk yet
        episode(6, "Last"),
    ]
    blocks = multipart_blocks(eps)
    assert ("show", 1, 2) not in blocks
    assert blocks[("show", 1, 5)] == ()


def test_a_marker_before_a_subtitle_continues_the_story() -> None:
    eps = [episode(1, "Weirdmageddon (1)"), episode(2, "Weirdmageddon (2): Escape from Reality")]
    assert multipart_blocks(eps)[("show", 1, 1)] == keys(1, 2)


def two_parters(programs: list[Program], pairs: int) -> list[Program]:
    """The first `pairs` pairs of episodes as two-part stories."""
    out = []
    for i, p in enumerate(programs):
        if i < pairs * 2:
            p = replace(p, episode_title=f"Story {i // 2} ({i % 2 + 1})")
        out.append(p)
    return out


def simpsons_library(programs: list[Program]) -> Library:
    base = make_library()
    episodes = dict(base.episodes)
    episodes["the-simpsons"] = tuple(programs)
    return Library.build(episodes, base.movies, base.bumpers)


def programs_only(slots: tuple[Slot, ...]) -> list[Slot]:
    return [s for s in slots if s.kind in ("episode", "movie", "filler")]


def test_the_parts_of_a_story_air_back_to_back(catalog: Catalog) -> None:
    eps = two_parters(weekly("the-simpsons", "The Simpsons", date(1989, 12, 17), 120, HALF), 50)
    scheduler = Scheduler(catalog, simpsons_library(eps), seed=7)
    parts = 0
    for day in (date(1997, 3, 4) + timedelta(days=n) for n in range(10)):
        slots = scheduler.day("ktvu", day)
        shown = programs_only(slots)
        for a, b in itertools.pairwise(shown):
            if a.episode_title.endswith("(1)"):
                parts += 1
                assert b.episode_title == a.episode_title[:-3] + "(2)", f"{a} then {b}"
            if b.episode_title.endswith("(2)"):
                assert a.episode_title == b.episode_title[:-3] + "(1)", f"{a} then {b}"
        assert not shown[0].episode_title.endswith("(2)")
        assert not shown[-1].episode_title.endswith("(1)")
        # Only a bumper between the parts.
        for a, b in itertools.pairwise(slots):
            if a.episode_title.endswith("(1)"):
                assert b.kind == "bumper" or b.episode_title.endswith("(2)")
    assert parts > 10  # the stories did air


def test_a_lone_part_never_airs(catalog: Catalog) -> None:
    eps = weekly("the-simpsons", "The Simpsons", date(1989, 12, 17), 8, HALF)
    eps[3] = replace(eps[3], episode_title="Missing Half (2)")  # its Part 1 isn't on disk
    eps[6] = replace(eps[6], episode_title="Not Yet (1)")  # nor is this one's Part 2
    scheduler = Scheduler(catalog, simpsons_library(eps), seed=7)
    titles = {
        s.episode_title
        for n in range(14)
        for s in scheduler.day("ktvu", date(1997, 3, 4) + timedelta(days=n))
    }
    assert "Episode 1" in titles
    assert "Missing Half (2)" not in titles
    assert "Not Yet (1)" not in titles


def test_a_story_waits_until_its_last_part_has_debuted(catalog: Catalog) -> None:
    """The debut rule holds for every part: a story whose Part 2 debuts next week isn't
    started this week (it would air Part 1 alone)."""
    eps = weekly("the-simpsons", "The Simpsons", date(1997, 1, 5), 6, HALF)
    eps[4] = replace(eps[4], episode_title="Season Ender (1)")  # airs Feb 2
    eps[5] = replace(eps[5], episode_title="Season Ender (2)")  # airs Feb 9
    scheduler = Scheduler(catalog, simpsons_library(eps), seed=7)
    before = {s.episode_title for s in scheduler.day("ktvu", date(1997, 2, 4))}
    after = [s.episode_title for s in scheduler.day("ktvu", date(1997, 2, 11))]
    assert "Season Ender (1)" not in before
    assert "Season Ender (1)" in after
