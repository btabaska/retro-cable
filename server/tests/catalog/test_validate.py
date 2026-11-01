from retro_cable.catalog import load_catalog, validate
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
        "gravity-falls: premiere must be within 1990-2010" in e
        for e in errors(make_catalog, synthetic=synthetic)
    )


def test_synthetic_needs_a_premiere_or_library(make_catalog: MakeCatalog) -> None:
    synthetic = "synthetic:\n  - { show: gravity-falls }\n"
    assert any(
        "gravity-falls: set premiere or library" in e
        for e in errors(make_catalog, synthetic=synthetic)
    )


def test_override_holiday_tags_reference_known_holidays(make_catalog: MakeCatalog) -> None:
    overrides = OVERRIDES.replace("[halloween]", "[festivus]")
    assert any(
        "the-simpsons S10E04: unknown holiday 'festivus'" in e
        for e in errors(make_catalog, overrides=overrides)
    )


def test_a_show_cannot_have_both_us_debut_and_synthetic_premiere(make_catalog: MakeCatalog) -> None:
    synthetic = SYNTHETIC + "  - { show: dragon-ball-z, premiere: 1998-03-17 }\n"
    assert any(
        "dragon-ball-z: both us_debut and a synthetic premiere" in e
        for e in errors(make_catalog, synthetic=synthetic)
    )


def test_overrides_and_synthetic_must_name_carried_shows(make_catalog: MakeCatalog) -> None:
    overrides = OVERRIDES.replace("show: the-simpsons", "show: the-simpsonz")
    synthetic = SYNTHETIC.replace("gravity-falls", "gravity-fallz")
    errs = errors(make_catalog, overrides=overrides, synthetic=synthetic)
    assert any("overrides: 'the-simpsonz' has no carriage" in e for e in errs)
    assert any("synthetic: 'gravity-fallz' has no carriage" in e for e in errs)
