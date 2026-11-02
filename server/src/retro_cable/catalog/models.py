"""Typed catalog inputs. Each *File model is one YAML file in catalog/; the JSON Schemas in
catalog/schema/ are generated from these models (`make catalog-schema`)."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
CUSTOM_PREFIX = "custom-"  # ids of channels made in /admin (see catalog/overlay.py)
ClockTime = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
HolidayMode = Literal["on", "specials_only", "off"]
Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- channels.yaml -------------------------------------------------------------


class Identity(Model):
    """How a channel appeared on the dial for a period (NBC was KRON 4, then KNTV 11)."""

    from_: date = Field(alias="from")
    until: date | None = None
    number: int = Field(ge=1, le=999)
    call_sign: str = Field(min_length=1, max_length=12)
    network: str | None = None


class Block(Model):
    """A named block inside a channel, such as Fox Kids on KTVU."""

    id: Slug
    name: str
    from_: date | None = Field(default=None, alias="from")
    until: date | None = None


class Channel(Model):
    id: Slug
    name: str
    type: Literal["network", "marathon", "movie"]
    launch: date | None = Field(default=None, description="Hidden before this date unless library")
    library: bool = Field(
        default=False,
        description="Always on the dial; episodes and movies still wait for their air dates",
    )
    playlist: Literal["ordered", "shuffled"] | None = Field(
        default=None, description="Movie channels only"
    )
    identity: list[Identity] = Field(min_length=1)
    blocks: list[Block] = []
    authenticity: int = Field(default=30, ge=0, le=100)
    transplant_mark: bool = Field(default=True, description="Mark transplanted shows in the guide")
    bumper_seconds: int = Field(
        default=15,
        ge=0,
        le=300,
        description="Bumper or promo inserted between programs; 0 for none",
    )


class ChannelsFile(Model):
    timezone: str = "America/New_York"
    year_range: tuple[int, int] = (1990, 2010)
    channels: list[Channel]


# --- holidays.yaml -------------------------------------------------------------


class FixedRule(Model):
    type: Literal["fixed"]
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)


class NthWeekdayRule(Model):
    type: Literal["nth_weekday"]
    n: int = Field(ge=-1, le=5, description="1-5, or -1 for the last one in the month")
    weekday: Weekday
    month: int = Field(ge=1, le=12)


class TaggedItem(Model):
    title: str
    kind: Literal["episode", "movie", "special"]
    channel: Slug | None = None
    premiere: int | None = Field(default=None, ge=1900, le=2010)


class Holiday(Model):
    id: Slug
    name: str
    rule: Annotated[FixedRule | NthWeekdayRule, Field(discriminator="type")]
    window_days: int = Field(ge=1, le=60, description="Days up to and including the holiday")
    strength: Literal["low", "medium", "strong"]
    default_mode: HolidayMode
    channels: dict[Slug, HolidayMode] = {}
    tagged: list[TaggedItem] = []


class HolidaysFile(Model):
    holidays: list[Holiday]


# --- carriage.yaml -------------------------------------------------------------


class Carriage(Model):
    """A show (or movie) airing on a channel for a window of dates."""

    show: Slug
    title: str = Field(description="Title as it appears in Plex, for matching")
    channel: Slug
    from_: date = Field(alias="from")
    until: date | None = None
    block: Slug | None = None
    kind: Literal["show", "movie"] = "show"
    weight: float = Field(default=1.0, gt=0)
    tags: list[Slug] = []


class CarriageFile(Model):
    carriage: list[Carriage] = []


# --- synthetic.yaml ------------------------------------------------------------


class Synthetic(Model):
    """A transplant: a show (or movie) given a made-up premiere inside 1990-2010 on one
    channel. Its earliest episode airs on that channel from `premiere`, the rest keep their
    spacing; every other channel keeps the real (or US debut) dates."""

    show: Slug
    channel: Slug = Field(description="The one channel the pretend premiere applies to")
    premiere: date = Field(description="The pretend first air date on that channel")
    mark: bool | None = Field(default=None, description="Override the channel's transplant_mark")


class SyntheticFile(Model):
    synthetic: list[Synthetic] = []


# --- overrides.yaml ------------------------------------------------------------

EpisodeKey = Annotated[str, Field(pattern=r"^S\d{1,3}E\d{1,4}$")]


class ShowOverride(Model):
    show: Slug
    plex_title: str | None = Field(default=None, description="Plex title if not the carriage title")
    us_debut: date | None = Field(default=None, description="Imports: first US air date")
    exclude: bool = False
    episodes: dict[EpisodeKey, date] = Field(default={}, description="Per-episode air dates")
    holiday_tags: dict[EpisodeKey, list[Slug]] = Field(
        default={}, description="Per-episode holiday ids, replacing the title tagger"
    )
    seasonal: bool = Field(
        default=False,
        description="Holiday specials show: tagged episodes air only in their holiday's season",
    )


class OverridesFile(Model):
    shows: list[ShowOverride] = []


# --- templates.yaml ------------------------------------------------------------


class Pool(Model):
    """One weighted entry in a day-part pool: a specific carried show, or every show with a tag."""

    show: Slug | None = None
    tag: Slug | None = None
    weight: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _one_of_show_or_tag(self) -> "Pool":
        if (self.show is None) == (self.tag is None):
            raise ValueError("a pool entry names exactly one of show or tag")
        return self


class DayPart(Model):
    """One part of a channel's day. The next part starts when its `start` passes, but only
    after the program already running has finished, so day-parts anchor the day."""

    weekday: Weekday
    start: ClockTime = Field(
        description="Local HH:MM the part begins; a day's first part starts at 00:00"
    )
    block: Slug | None = Field(default=None, description="Restrict the pool to this channel block")
    primetime: bool = Field(default=False, description="This part airs NEW episodes on debut day")
    pool: list[Pool] = Field(
        default_factory=list, description="An empty pool runs bumpers until the next part"
    )


class Template(Model):
    """An era version of a channel's week: CN 1992-96 leans on classics, 1997+ adds originals."""

    id: Slug
    channel: Slug
    from_: date = Field(alias="from")
    until: date | None = None
    parts: list[DayPart] = Field(min_length=1)


class TemplatesFile(Model):
    templates: list[Template] = []


FILE_MODELS: dict[str, type[Model]] = {
    "channels": ChannelsFile,
    "holidays": HolidaysFile,
    "carriage": CarriageFile,
    "synthetic": SyntheticFile,
    "overrides": OverridesFile,
    "templates": TemplatesFile,
}
