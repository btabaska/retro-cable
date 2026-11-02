from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from retro_cable.catalog import effective_dates, load_catalog, premiere_for, shift_air_dates
from retro_cable.catalog.air_dates import premiere_dates, synthesize_undated
from retro_cable.catalog.models import Carriage, Channel
from retro_cable.catalog.overlay import Overlay, apply_overlay
from tests.conftest import OVERRIDES, SYNTHETIC, MakeCatalog


def test_no_anchor_keeps_original_dates() -> None:
    dates = [date(1999, 1, 1), date(1999, 1, 8)]
    assert shift_air_dates(dates, None) == dates


def test_anchor_moves_the_first_episode_and_keeps_spacing() -> None:
    original = [date(2012, 6, 15), date(2012, 6, 29), None, date(2012, 7, 13)]
    assert shift_air_dates(original, date(2004, 6, 15)) == [
        date(2004, 6, 15),
        date(2004, 6, 29),
        None,
        date(2004, 7, 13),
    ]


def test_all_missing_dates_stay_missing() -> None:
    assert shift_air_dates([None, None], date(2004, 1, 1)) == [None, None]


def test_effective_dates_apply_episode_overrides_then_the_anchor(make_catalog: MakeCatalog) -> None:
    overrides = OVERRIDES + (
        "  - show: dexters-laboratory\n    episodes:\n      S1E2: 1996-05-05\n"
    )
    catalog = load_catalog(make_catalog(overrides=overrides))
    episodes = [(1, 1, date(1996, 4, 28)), (1, 2, None), (1, 3, date(1996, 5, 12))]
    assert effective_dates(catalog, "dexters-laboratory", episodes) == [
        date(1996, 4, 28),
        date(1996, 5, 5),
        date(1996, 5, 12),
    ]


def test_effective_dates_use_us_debut_and_synthetic_premiere(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    dbz = [(1, 1, date(1989, 4, 26)), (1, 2, date(1989, 5, 3))]
    assert effective_dates(catalog, "dragon-ball-z", dbz) == [date(1996, 9, 13), date(1996, 9, 20)]
    gf = [(1, 1, date(2012, 6, 15))]
    assert effective_dates(catalog, "gravity-falls", gf, channel="cartoon-network") == [
        date(2004, 6, 15)
    ]


# --- synthetic premieres belong to one (show, channel) pair (M2 A3, A4) -----------------


def test_a_synthetic_premiere_applies_only_on_its_channel(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    gf = [(1, 1, date(2012, 6, 15)), (1, 2, date(2012, 6, 29))]
    assert premiere_for(catalog, "gravity-falls", "cartoon-network") is not None
    assert premiere_for(catalog, "gravity-falls", "ktvu") is None
    assert premiere_for(catalog, "gravity-falls", None) is None
    # Every other channel (and no channel) keeps the real dates.
    for channel in (None, "ktvu", "disney-movies", "custom-toons"):
        assert effective_dates(catalog, "gravity-falls", gf, channel=channel) == [
            date(2012, 6, 15),
            date(2012, 6, 29),
        ]
    assert effective_dates(catalog, "gravity-falls", gf, channel="cartoon-network") == [
        date(2004, 6, 15),
        date(2004, 6, 29),
    ]


def test_a_premiere_on_one_channel_keeps_the_us_debut_elsewhere(make_catalog: MakeCatalog) -> None:
    # DBZ has a US debut (1996-09-13) in the overrides; a pretend premiere on Cartoon Network
    # replaces it there only.
    synthetic = (
        SYNTHETIC + "  - { show: dragon-ball-z, channel: cartoon-network, premiere: 1993-01-04 }\n"
    )
    catalog = load_catalog(make_catalog(synthetic=synthetic))
    dbz = [(1, 1, date(1989, 4, 26)), (1, 2, date(1989, 5, 3))]
    assert effective_dates(catalog, "dragon-ball-z", dbz, channel="cartoon-network") == [
        date(1993, 1, 4),
        date(1993, 1, 11),
    ]
    assert effective_dates(catalog, "dragon-ball-z", dbz, channel="ktvu") == [
        date(1996, 9, 13),
        date(1996, 9, 20),
    ]


def test_premiere_moves_s1e1_and_keeps_the_spacing() -> None:
    keys = [(1, 1), (1, 2), (2, 1)]
    dates = [date(2012, 6, 15), date(2012, 6, 29), date(2013, 8, 2)]
    assert premiere_dates(keys, dates, date(2004, 6, 15)) == [
        date(2004, 6, 15),
        date(2004, 6, 29),
        date(2005, 8, 2),
    ]


def test_premiere_gives_undated_episodes_a_weekly_cadence_after_the_last_dated_one() -> None:
    keys = [(1, 1), (1, 2), (1, 3), (1, 4), (2, 1)]
    dates = [date(2012, 6, 15), date(2012, 6, 29), None, None, None]
    assert premiere_dates(keys, dates, date(2004, 6, 15)) == [
        date(2004, 6, 15),
        date(2004, 6, 29),
        date(2004, 7, 6),
        date(2004, 7, 13),
        date(2004, 7, 20),
    ]


def test_premiere_on_a_show_with_no_dates_runs_weekly_from_the_premiere() -> None:
    keys = [(2, 1), (1, 1), (1, 2), (0, 1)]  # not in air order; a special stays undated
    assert premiere_dates(keys, [None] * 4, date(2004, 6, 15)) == [
        date(2004, 6, 29),
        date(2004, 6, 15),
        date(2004, 6, 22),
        None,
    ]


@given(
    episodes=st.lists(
        st.tuples(st.integers(1, 9), st.integers(1, 40), st.none() | st.dates(
            min_value=date(1960, 1, 1), max_value=date(2025, 12, 31))),
        min_size=1, max_size=40, unique_by=lambda e: (e[0], e[1]),
    ),
    premiere=st.dates(min_value=date(1990, 1, 1), max_value=date(2010, 12, 31)),
)  # fmt: skip
def test_premiere_dates_never_precede_the_premiere(
    episodes: list[tuple[int, int, date | None]], premiere: date
) -> None:
    keys = [(s, e) for s, e, _ in episodes]
    out = premiere_dates(keys, [d for _, _, d in episodes], premiere)
    assert all(d is not None and d >= premiere for d in out)
    dated = [d for _, _, d in episodes if d is not None]
    if dated:  # the earliest dated episode lands on the premiere
        first = min(range(len(episodes)), key=lambda i: episodes[i][2] or date.max)
        assert out[first] == premiere


# --- undated episodes (`ingest scan` without Plex or TMDB dates) ------------------------


def test_undated_episodes_count_from_the_us_debut_in_half_months(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    eps = [(1, 1, None), (1, 2, None), (2, 1, None), (2, 3, None)]
    assert effective_dates(catalog, "dragon-ball-z", eps) == [
        date(1996, 9, 13),  # the US debut itself
        date(1996, 9, 29),  # 16 days per episode
        date(1997, 9, 13),  # a season per year
        date(1997, 10, 15),
    ]


def test_undated_episodes_without_a_debut_wait_until_the_end_of_the_premiere_year(
    make_catalog: MakeCatalog,
) -> None:
    catalog = load_catalog(make_catalog())
    eps = [(1, 1, None), (1, 2, None), (3, 1, None)]
    # The Simpsons (1989): Dec 31 1989 is never before the real debut (Dec 17 1989).
    assert effective_dates(catalog, "the-simpsons", eps, first_year=1989) == [
        date(1989, 12, 31),
        date(1990, 1, 16),
        date(1991, 12, 31),
    ]


def test_without_a_year_the_first_network_carriage_year_bounds_the_debut(
    make_catalog: MakeCatalog,
) -> None:
    catalog = load_catalog(make_catalog())
    # Dexter's Laboratory is carried on Cartoon Network from 1996-04-28.
    assert effective_dates(catalog, "dexters-laboratory", [(1, 1, None)]) == [date(1996, 12, 31)]
    # Only carried on a library channel: nothing bounds the debut, so it stays undated.
    assert effective_dates(catalog, "halloweentown", [(1, 1, None)]) == [None]


def test_specials_and_year_numbered_seasons_stay_undated(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    eps = [(0, 1, None), (1, 0, None), (1998, 3, None)]
    assert effective_dates(catalog, "the-simpsons", eps, first_year=1989) == [None, None, None]


def test_an_undated_episode_never_airs_before_a_dated_one_before_it(
    make_catalog: MakeCatalog,
) -> None:
    catalog = load_catalog(make_catalog())
    eps = [(1, 1, date(1990, 6, 1)), (1, 2, None), (1, 3, date(1990, 7, 1))]
    assert effective_dates(catalog, "the-simpsons", eps, first_year=1989) == [
        date(1990, 6, 1),
        date(1990, 6, 1),  # synthesized Jan 16 1990, raised to the episode before it
        date(1990, 7, 1),
    ]


@given(
    episodes=st.lists(
        st.tuples(st.integers(1, 30), st.integers(1, 120)), min_size=1, max_size=60, unique=True
    ),
    year=st.integers(1960, 2010),
)
def test_synthesized_dates_never_precede_the_bound_and_follow_air_order(
    episodes: list[tuple[int, int]], year: int
) -> None:
    keys = sorted(episodes)
    dates = synthesize_undated(keys, [None] * len(keys), date(year, 12, 31))
    known = [d for d in dates if d is not None]
    assert len(known) == len(keys)
    assert all(d >= date(year, 12, 31) for d in known)
    assert known == sorted(known)


def test_an_uncarried_show_is_bounded_by_its_folder_year_or_stays_undated(
    make_catalog: MakeCatalog,
) -> None:
    catalog = load_catalog(make_catalog())
    # Scanned from "Johnny Bravo (1997)" but carried by no channel.
    assert effective_dates(catalog, "johnny-bravo", [(1, 1, None)], first_year=1997) == [
        date(1997, 12, 31)
    ]
    assert effective_dates(catalog, "johnny-bravo", [(1, 1, None)]) == [None]


def test_custom_channel_carriage_never_bounds_a_debut(make_catalog: MakeCatalog) -> None:
    # Putting a show on an admin-made channel says nothing about when it first aired: a custom
    # channel launching in 1990 must not date an undated show 1990-12-31.
    catalog = load_catalog(make_catalog())
    custom = Channel.model_validate(
        {"id": "custom-toons", "name": "Toons", "type": "network", "launch": "1990-01-01",
         "identity": [{"from": "1990-01-01", "number": 50, "call_sign": "TOONS"}]}
    )  # fmt: skip
    carriage = [
        Carriage.model_validate(
            {"show": show, "title": title, "channel": "custom-toons", "from": "1990-01-01"}
        )
        for show, title in (
            ("johnny-bravo", "Johnny Bravo"),
            ("dexters-laboratory", "Dexter's Laboratory"),
        )
    ]
    effective = apply_overlay(catalog, Overlay(channels=[custom], carriage=carriage))
    assert effective_dates(effective, "johnny-bravo", [(1, 1, None)]) == [None]
    # YAML carriage still bounds it: Cartoon Network from 1996.
    assert effective_dates(effective, "dexters-laboratory", [(1, 1, None)]) == [date(1996, 12, 31)]
