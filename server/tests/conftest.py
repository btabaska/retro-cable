from collections.abc import Callable
from pathlib import Path
from textwrap import dedent

import pytest

CHANNELS = """
timezone: America/New_York
year_range: [1990, 2010]
channels:
  - id: ktvu
    name: KTVU (Fox)
    type: network
    identity:
      - { from: 1990-01-01, number: 2, call_sign: KTVU, network: FOX }
    blocks:
      - { id: fox-kids, name: Fox Kids, until: 2002-09-01 }
  - id: cartoon-network
    name: Cartoon Network
    type: network
    launch: 1992-10-01
    identity:
      - { from: 1992-10-01, number: 27, call_sign: TOON }
  - id: disney-movies
    name: Disney Movies
    type: movie
    library: true
    playlist: ordered
    identity:
      - { from: 1990-01-01, number: 31, call_sign: DMOV }
    authenticity: 100
"""

HOLIDAYS = """
holidays:
  - id: halloween
    name: Halloween
    rule: { type: fixed, month: 10, day: 31 }
    window_days: 7
    strength: strong
    default_mode: "on"
  - id: thanksgiving
    name: Thanksgiving
    rule: { type: nth_weekday, n: 4, weekday: thursday, month: 11 }
    window_days: 3
    strength: medium
    default_mode: "on"
    channels:
      cartoon-network: specials_only
"""

CARRIAGE = """
carriage:
  - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 1990-01-01 }
  - { show: x-men, title: X-Men, channel: ktvu, block: fox-kids, from: 1992-10-31, until: 1997-09-20 }
  - { show: dexters-laboratory, title: Dexter's Laboratory, channel: cartoon-network, from: 1996-04-28 }
  - { show: dragon-ball-z, title: Dragon Ball Z, channel: cartoon-network, from: 1998-03-16 }
  - { show: gravity-falls, title: Gravity Falls, channel: cartoon-network, from: 2004-06-15 }
  - { show: halloweentown, title: Halloweentown, channel: disney-movies, from: 1990-01-01, kind: movie }
"""

SYNTHETIC = """
synthetic:
  - { show: gravity-falls, channel: cartoon-network, premiere: 2004-06-15 }
"""

OVERRIDES = """
shows:
  - show: dragon-ball-z
    us_debut: 1996-09-13
    plex_title: "Dragon Ball Z (1989)"
  - show: the-simpsons
    holiday_tags:
      S10E04: [halloween]
"""

TEMPLATES = """
templates:
  - id: ktvu-base
    channel: ktvu
    from: 1990-01-01
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: the-simpsons }] }
      - { weekday: friday, start: "16:00", block: fox-kids, pool: [{ show: x-men }] }
      - { weekday: friday, start: "20:00", primetime: true, pool: [{ show: the-simpsons, weight: 2 }, { show: x-men }] }
  - id: cn-classics
    channel: cartoon-network
    from: 1992-10-01
    until: 1996-12-31
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: dexters-laboratory, weight: 2 }] }
"""  # fmt: skip

FILES = {
    "channels.yaml": CHANNELS,
    "holidays.yaml": HOLIDAYS,
    "carriage.yaml": CARRIAGE,
    "synthetic.yaml": SYNTHETIC,
    "overrides.yaml": OVERRIDES,
    "templates.yaml": TEMPLATES,
}

MakeCatalog = Callable[..., Path]


@pytest.fixture
def make_catalog(tmp_path: Path) -> MakeCatalog:
    """Write a small valid catalog; keyword args replace a file's text (None removes it)."""

    def make(**replace: str | None) -> Path:
        root = tmp_path / "catalog"
        root.mkdir(exist_ok=True)
        for name, text in FILES.items():
            key = name.removesuffix(".yaml")
            body = replace.get(key, text)
            path = root / name
            if body is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(dedent(body))
        return root

    return make
