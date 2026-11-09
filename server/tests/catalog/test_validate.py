import pytest

from retro_cable.catalog import CatalogError, load_catalog, validate
from tests.conftest import CARRIAGE, CHANNELS, HOLIDAYS, OVERRIDES, SYNTHETIC, MakeCatalog


def errors(make_catalog: MakeCatalog, **replace: str | None) -> list[str]:
    return validate(load_catalog(make_catalog(**replace)))


def test_the_sample_catalog_is_valid(make_catalog: MakeCatalog) -> None:
    assert errors(make_catalog) == []


def test_duplicate_channel_ids(make_catalog: MakeCatalog) -> None:
    dup = CHANNELS.replace("id: cartoon-network", "id: ktvu")
    assert any("duplicate channel id 'ktvu'" in e for e in errors(make_catalog, channels=dup))


def test_identity_periods_must_not_overlap(make_catalog: MakeCatalog) -> None:
    channels = CHANNELS.replace(
        "      - { from: 1990-01-01, number: 2, call_sign: KTVU, network: FOX }",
        "      - { from: 1990-01-01, until: 2001-12-31, number: 4, call_sign: KRON }\n"
        "      - { from: 2001-06-01, number: 11, call_sign: KNTV }",
    )
    assert any(
        "ktvu: identity periods overlap" in e for e in errors(make_catalog, channels=channels)
    )


def test_identity_must_cover_launch(make_catalog: MakeCatalog) -> None:
    channels = CHANNELS.replace("{ from: 1992-10-01, number: 27", "{ from: 1993-01-01, number: 27")
    assert any(
        "cartoon-network: no identity on launch" in e
        for e in errors(make_catalog, channels=channels)
    )


def test_movie_channels_need_a_playlist(make_catalog: MakeCatalog) -> None:
    channels = CHANNELS.replace("    playlist: ordered\n", "")
    assert any(
        "disney-movies: movie channels need a playlist" in e
        for e in errors(make_catalog, channels=channels)
    )


def test_year_range_is_within_1990_2010(make_catalog: MakeCatalog) -> None:
    channels = CHANNELS.replace("[1990, 2010]", "[1989, 2010]")
    assert any("year_range" in e for e in errors(make_catalog, channels=channels))


def test_unknown_timezone(make_catalog: MakeCatalog) -> None:
    channels = CHANNELS.replace("America/New_York", "Mars/Olympus_Mons")
    assert any("timezone" in e for e in errors(make_catalog, channels=channels))


def test_carriage_references_known_channels_and_blocks(make_catalog: MakeCatalog) -> None:
    carriage = CARRIAGE.replace("channel: cartoon-network", "channel: cartoon-netwerk").replace(
        "block: fox-kids", "block: fox-box"
    )
    errs = errors(make_catalog, carriage=carriage)
    assert any("unknown channel 'cartoon-netwerk'" in e for e in errs)
    assert any("unknown block 'fox-box' on ktvu" in e for e in errs)


def test_carriage_windows_for_one_show_and_channel_must_not_overlap(
    make_catalog: MakeCatalog,
) -> None:
    carriage = (
        CARRIAGE
        + "  - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 2000-01-01 }\n"
    )
    assert any(
        "the-simpsons on ktvu: carriage windows overlap" in e
        for e in errors(make_catalog, carriage=carriage)
    )


def test_carriage_ends_after_it_starts(make_catalog: MakeCatalog) -> None:
    carriage = CARRIAGE.replace("until: 1997-09-20", "until: 1991-01-01")
    assert any(
        "x-men on ktvu: until is before from" in e for e in errors(make_catalog, carriage=carriage)
    )


def test_holiday_channels_must_exist(make_catalog: MakeCatalog) -> None:
    holidays = HOLIDAYS.replace("cartoon-network: specials_only", "toonami: specials_only")
    assert any(
        "thanksgiving: unknown channel 'toonami'" in e
        for e in errors(make_catalog, holidays=holidays)
    )


def test_fixed_holiday_dates_must_exist(make_catalog: MakeCatalog) -> None:
    holidays = HOLIDAYS.replace("month: 10, day: 31", "month: 11, day: 31")
    assert any("halloween: no such date" in e for e in errors(make_catalog, holidays=holidays))


def test_synthetic_premieres_fall_inside_the_dial_years(make_catalog: MakeCatalog) -> None:
    synthetic = SYNTHETIC.replace("2004-06-15", "2012-06-15")
    assert any(
        "gravity-falls on cartoon-network: premiere must be within 1990-2010" in e
        for e in errors(make_catalog, synthetic=synthetic)
    )


def test_synthetic_needs_a_channel_and_a_premiere(make_catalog: MakeCatalog) -> None:
    # A synthetic premiere belongs to one (show, channel) pair, and `library: true` is gone
    # from synthetic.yaml (M2: the debut rule holds on every channel).
    for body in (
        "synthetic:\n  - { show: gravity-falls, premiere: 2004-06-15 }\n",
        "synthetic:\n  - { show: gravity-falls, channel: cartoon-network }\n",
        "synthetic:\n  - { show: gravity-falls, channel: cartoon-network, premiere: 2004-06-15,"
        " library: true }\n",
    ):
        with pytest.raises(CatalogError, match=r"synthetic\.yaml is invalid"):
            load_catalog(make_catalog(synthetic=body))


def test_synthetic_premieres_are_unique_per_show_and_channel(make_catalog: MakeCatalog) -> None:
    synthetic = (
        SYNTHETIC + "  - { show: gravity-falls, channel: cartoon-network, premiere: 2005-01-01 }\n"
    )
    assert any(
        "synthetic: 'gravity-falls' on cartoon-network is listed twice" in e
        for e in errors(make_catalog, synthetic=synthetic)
    )


def test_override_holiday_tags_reference_known_holidays(make_catalog: MakeCatalog) -> None:
    overrides = OVERRIDES.replace("[halloween]", "[festivus]")
    assert any(
        "the-simpsons S10E04: unknown holiday 'festivus'" in e
        for e in errors(make_catalog, overrides=overrides)
    )


def test_a_show_can_have_a_us_debut_and_a_premiere_on_one_channel(
    make_catalog: MakeCatalog,
) -> None:
    # The premiere replaces the US debut on its own channel only (M2 A3), so both may be set.
    synthetic = (
        SYNTHETIC + "  - { show: dragon-ball-z, channel: cartoon-network, premiere: 1998-03-17 }\n"
    )
    assert errors(make_catalog, synthetic=synthetic) == []


def test_overrides_and_synthetic_must_name_carried_shows(make_catalog: MakeCatalog) -> None:
    overrides = OVERRIDES.replace("show: the-simpsons", "show: the-simpsonz")
    synthetic = SYNTHETIC.replace("gravity-falls", "gravity-fallz")
    errs = errors(make_catalog, overrides=overrides, synthetic=synthetic)
    assert any("overrides: 'the-simpsonz' has no carriage" in e for e in errs)
    assert any("synthetic: 'gravity-fallz' is not carried on cartoon-network" in e for e in errs)


def test_a_lookup_only_override_may_name_a_show_admin_channels_carry(
    make_catalog: MakeCatalog,
) -> None:
    """The channel builder's shows are carried in /admin, not the YAML. An override that only
    helps find one (tmdb_id, year, plex_title) is allowed; one that changes airing is not."""
    lookup = (
        OVERRIDES
        + '  - { show: bluey, tmdb_id: "tv:82728", year: 2019, plex_title: "Bluey 2018" }\n'
    )
    assert errors(make_catalog, overrides=lookup) == []
    airing = OVERRIDES + "  - { show: bluey, year: 2019, seasonal: true }\n"
    assert any("overrides: 'bluey' has no carriage" in e
               for e in errors(make_catalog, overrides=airing))  # fmt: skip


def test_a_synthetic_premiere_names_a_channel_that_carries_the_show(
    make_catalog: MakeCatalog,
) -> None:
    synthetic = SYNTHETIC.replace("channel: cartoon-network", "channel: ktvu")
    assert any(
        "synthetic: 'gravity-falls' is not carried on ktvu" in e
        for e in errors(make_catalog, synthetic=synthetic)
    )


def test_a_season_of_a_series_airs_as_one_show_only(make_catalog: MakeCatalog) -> None:
    part = "part_of: {{ show: {series}, tvdb_id: 1, seasons: [{seasons}] }}"
    ok = OVERRIDES + (
        f"  - {{ show: x-men, {part.format(series='the-simpsons', seasons='2')} }}\n"
        f"  - {{ show: bluey, {part.format(series='bluey-1976', seasons='1')} }}\n"
    )
    assert errors(make_catalog, overrides=ok) == []  # bluey: carried in /admin
    twice = OVERRIDES + (
        f"  - {{ show: x-men, {part.format(series='the-simpsons', seasons='2, 3')} }}\n"
        f"  - {{ show: gravity-falls, {part.format(series='the-simpsons', seasons='3')} }}\n"
    )
    assert any(
        "overrides: the-simpsons season 3 airs as both x-men and gravity-falls" in e
        for e in errors(make_catalog, overrides=twice)
    )
    itself = OVERRIDES + f"  - {{ show: x-men, {part.format(series='x-men', seasons='2')} }}\n"
    assert any(
        "overrides: 'x-men' can't be part of itself" in e
        for e in errors(make_catalog, overrides=itself)
    )


def test_a_tv_movie_made_of_episodes(make_catalog: MakeCatalog) -> None:
    """overrides.yaml `episodes_of`: lookup-only, so a movie on an /admin channel may have
    it; not its own series, each episode once, and not also seasons of a series."""
    of = "episodes_of: {{ show: {series}, episodes: [{episodes}] }}"
    ok = OVERRIDES + (
        f"  - {{ show: really-big-shrimp, year: 2007,"
        f" {of.format(series='drake-and-josh', episodes='S04E17, S04E18')} }}\n"
    )
    assert errors(make_catalog, overrides=ok) == []
    bad = OVERRIDES + (
        f"  - {{ show: halloweentown, {of.format(series='halloweentown', episodes='S01E01')} }}\n"
        f"  - {{ show: really-big-shrimp,"
        f" {of.format(series='drake-and-josh', episodes='S04E17, S4E17')} }}\n"
        f"  - {{ show: sozins-comet, part_of: {{ show: avatar, tvdb_id: 1, seasons: [3] }},"
        f" {of.format(series='avatar', episodes='S03E18')} }}\n"
    )
    found = errors(make_catalog, overrides=bad)
    assert "overrides: 'halloweentown' can't be made of its own episodes" in found
    assert "overrides: 'really-big-shrimp' lists S04E17 twice" in found
    assert "overrides: 'sozins-comet' is part_of a series or episodes_of one, not both" in found
