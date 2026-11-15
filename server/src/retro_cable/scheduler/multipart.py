"""Multi-part stories: episodes whose titles number them as parts of one story.

"The Best of Both Worlds (1)" and "(2)", "Who Shot Mr. Burns? Part One" and "Part Two": the
scheduler airs a story's parts back to back, as one block, and never one part on its own
(Brandon, Nov 15 2026). A story is found two ways, in one show's episodes in air order:

- parts with the same title apart from the marker, numbered 1, 2, 3..., wherever they are
  (Animaniacs puts a segment's parts in different episodes);
- consecutive episodes numbered 1, 2, 3... whatever their titles (Yu-Gi-Oh!'s "Into the
  Hornet's Nest Part 1 of 2" then "The Ultimate Great Moth Part 2 of 2"), where a Part 2's
  unmarked predecessor starting with its title is Part 1 (The Simpsons' "Warrin Priests" then
  "Warrin Priests Part 2"), and an untitled episode right after a Part 1 is Part 2.

A file holding both parts ("The Cadillac Part 1 and The Cadillac Part 2") is one episode. A
part left without its story (its Part 1 isn't on disk, or nothing follows a Part 1) is held
back until the rest arrives.
"""

import re
from collections import defaultdict
from collections.abc import Sequence

from retro_cable.scheduler.library import Program

ProgramKey = tuple[str, int | None, int | None]

_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
}  # fmt: skip
_N = r"(\d{1,2}|one|two|three|four|five|six|i{1,3}|iv|vi?)"
# "(1)", "(Part 2)", "(2 of 3)" at the end, or "Part 1", "Pt. II", ": Part Two" at the end.
_MARKERS = (
    re.compile(rf"\s*\(\s*(?:part\s+|pt\.?\s*)?{_N}(?:\s+of\s+\d+)?\s*\)\s*$", re.I),
    re.compile(rf"[\s,:;\-\u2013\u2014]+(?:part|pt\.?)\s*{_N}(?:\s+of\s+\d+)?\s*$", re.I),
)


def _number(text: str) -> int:
    return int(text) if text.isdigit() else _NUMBERS[text.lower()]


def _base(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))


# A Part 1 marker before the last one: the file holds both parts.
_FIRST = re.compile(r"(?:\b(?:part|pt\.?)\s*(?:1|one|i)\b|\(\s*1\s*\))", re.I)


def part_marker(title: str) -> tuple[str, int] | None:
    """(the story's title, normalized; the part number), or None for a whole episode."""
    for marker in _MARKERS:
        m = marker.search(title)
        if m and m.start() > 0:
            base = _base(title[: m.start()])
            n = _number(m[1])
            if base and not (n > 1 and _FIRST.search(title[: m.start()])):
                return base, n
            return None
    return None


def _key(p: Program) -> ProgramKey:
    return (p.show, p.season, p.episode)


def multipart_blocks(programs: Sequence[Program]) -> dict[ProgramKey, tuple[ProgramKey, ...]]:
    """Each part of a story in one show's episodes (in air order) -> the story's parts in
    order; () for a part held back. Episodes that aren't parts are left out."""
    eps = list(programs)
    marks = [part_marker(p.episode_title) for p in eps]
    number = [m[1] if m else 0 for m in marks]
    blocks: dict[ProgramKey, tuple[ProgramKey, ...]] = {}
    taken: set[int] = set()

    def story(indices: list[int]) -> None:
        keys = tuple(_key(eps[i]) for i in indices)
        for i in indices:
            blocks[_key(eps[i])] = keys
        taken.update(indices)

    by_title: dict[str, list[int]] = defaultdict(list)
    for i, m in enumerate(marks):
        if m:
            by_title[m[0]].append(i)
    for indices in by_title.values():
        numbered = sorted(indices, key=lambda i: number[i])
        if len(indices) > 1 and [number[i] for i in numbered] == list(range(1, len(indices) + 1)):
            story(numbered)

    def free(i: int, season: int | None) -> bool:
        return 0 <= i < len(eps) and i not in taken and eps[i].season == season

    i = 0
    while i < len(eps):
        mark = marks[i]
        if mark is None or i in taken:
            i += 1
            continue
        run = [i]
        while free(run[-1] + 1, eps[i].season) and number[run[-1] + 1] == number[run[-1]] + 1:
            run.append(run[-1] + 1)
        first, prev, after = mark[1], i - 1, run[-1] + 1
        if (
            first == 2
            and free(prev, eps[i].season)
            and marks[prev] is None
            and _base(eps[prev].episode_title).startswith(mark[0])
        ):
            run.insert(0, prev)
            first = 1
        if (
            first == 1
            and len(run) == 1
            and free(after, eps[i].season)
            and marks[after] is None
            and not eps[after].episode_title.strip()
        ):
            run.append(after)
        if first == 1 and len(run) > 1:
            story(run)
        else:
            for j in run:
                blocks[_key(eps[j])] = ()
        i = run[-1] + 1
    return blocks
