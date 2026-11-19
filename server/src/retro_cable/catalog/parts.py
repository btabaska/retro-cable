"""Shows TheTVDB files as seasons of another series (overrides.yaml `part_of`).

Justice League Unlimited is Justice League seasons 3-5 on TheTVDB, so Sonarr downloads it into
the Justice League folder and the library keeps those files under `justice-league`. The
scheduler reads them as `justice-league-unlimited` (Library.split), acquire asks Sonarr for
just those seasons, and the rig converts the series' files for the show that carries them.
"""

from dataclasses import dataclass

from retro_cable.catalog.load import Catalog


@dataclass(frozen=True)
class Part:
    show: str  # the show these seasons air as
    title: str
    year: int | None  # its first-aired year (overrides.yaml `year`), for undated episodes


def season_parts(catalog: Catalog) -> dict[tuple[str, int], Part]:
    """(series id, season) -> the show that season airs as. Excluded shows claim nothing."""
    titles: dict[str, str] = {}
    for c in catalog.carriage.carriage:
        titles.setdefault(c.show, c.title)
    excluded = {o.show for o in catalog.overrides.shows if o.exclude}
    parts: dict[tuple[str, int], Part] = {}
    for o in catalog.overrides.shows:
        if o.part_of is None or o.show in excluded:
            continue
        part = Part(o.show, titles.get(o.show, o.show), o.year)
        for season in o.part_of.seasons:
            parts.setdefault((o.part_of.show, season), part)
    return parts
