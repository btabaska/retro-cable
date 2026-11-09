"""The /admin API. LAN-only like the rest of the app, with no auth in M1.

Every write changes inputs only: it appends an overlay revision (store.py), which the
schedules merge into the catalog and its version. Nothing here writes a schedule.
"""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from retro_cable import tv
from retro_cable.admin import store
from retro_cable.admin.drafts import (
    ChannelDraft,
    Checked,
    CheckIssue,
    CheckResult,
    LibraryItem,
    check_draft,
    keep_templates,
    new_channel_id,
    premiere_issues,
    preview_week,
    to_draft,
)
from retro_cable.catalog import Catalog, validate
from retro_cable.catalog.models import Template
from retro_cable.catalog.overlay import (
    CUSTOM_PREFIX,
    Overlay,
    apply_overlay,
    with_premiere,
    without_channel,
)
from retro_cable.db import connect, migrate
from retro_cable.scheduler import ChannelOffAir, Library, Scheduler

DEFAULT_YEAR = 2000
LAST_DAY = date(2010, 12, 31)
MonthDay = Annotated[str | None, Field(pattern=r"^\d{2}-\d{2}$")]


class AdminState(BaseModel):
    revision: int
    catalog_version: str
    can_undo: bool


class AdminChannelRow(BaseModel):
    id: str
    name: str
    number: int
    call_sign: str
    type: Literal["network", "marathon", "movie"]
    library: bool
    launch: date | None
    custom: bool = Field(description="Made in /admin (editable); YAML channels are read-only")
    shows: int = Field(description="Shows and movies carried, in any year")


class AdminChannelList(BaseModel):
    state: AdminState
    channels: list[AdminChannelRow]


class AdminChannelShow(BaseModel):
    """A carried show's dates on one channel, for the pretend premiere control."""

    show: str
    kind: Literal["show", "movie"]
    debut: date | None = Field(description="Real (or US debut) first air date; no pretend dates")
    premiere: date | None = Field(description="Pretend premiere on this channel, if any")
    premiere_source: Literal["admin", "catalog"] | None = Field(
        description="Where the premiere is set: /admin (editable) or catalog/synthetic.yaml"
    )
    first_air: date | None = Field(description="First episode's date on this channel (S1E1)")
    last_air: date | None = Field(description="Last episode's date on this channel")
    on_air_until: date | None = Field(
        description="Last day the channel carries it (None: open-ended, through 2010)"
    )
    airs: bool = Field(description="Something of it can air on this channel by its last day")


class AdminChannel(BaseModel):
    id: str
    custom: bool
    draft: ChannelDraft
    shows: list[AdminChannelShow] = Field(default_factory=list)


class PremiereRequest(BaseModel):
    premiere: date | None = Field(description="The pretend premiere; null clears it")


class PremiereResult(BaseModel):
    message: str
    show: AdminChannelShow
    warnings: list[CheckIssue]
    state: AdminState


class DraftRequest(BaseModel):
    draft: ChannelDraft
    channel_id: str | None = Field(default=None, description="The channel being edited, if any")
    year: int = Field(default=DEFAULT_YEAR, ge=1990, le=2010, description="Year to check")
    date: MonthDay = Field(default=None, description="MM-DD of the week; default today's")


class PreviewDay(BaseModel):
    date: date
    weekday: str
    on_air: bool
    slots: list[tv.GuideSlot]


class Preview(BaseModel):
    check: CheckResult
    channel: tv.ChannelInfo | None
    days: list[PreviewDay]


class SaveResult(BaseModel):
    channel_id: str
    message: str
    check: CheckResult
    state: AdminState


class ChangeResult(BaseModel):
    message: str
    state: AdminState


class LibraryChannel(BaseModel):
    id: str
    name: str
    number: int


class LibraryEntry(BaseModel):
    id: str
    title: str
    kind: Literal["show", "movie"]
    first_year: int | None
    last_year: int | None
    debut: date | None = Field(default=None, description="Real (or US debut) first air date")
    episodes: int = Field(description="Episodes (or the movie) on disk")
    channels: list[LibraryChannel] = Field(description="Channels that carry it, in any year")
    tmdb_id: str | None = Field(
        default=None, description="TMDB id from the lineup (tv:N or movie:N), for ingest acquire"
    )
    year: int | None = Field(default=None, description="First-aired year from the lineup")
    shifted: bool = Field(
        default=False,
        description="Airs on moved dates somewhere: a pretend premiere on some channel or a US"
        " debut (ingest acquire keeps all its seasons)",
    )


class LibraryList(BaseModel):
    items: list[LibraryEntry]


class Schedules(Protocol):
    """What the admin router needs from the app's schedule cache."""

    def __call__(self) -> Scheduler: ...

    @property
    def base(self) -> Catalog:
        """The YAML catalog, without the overlay."""
        ...

    @property
    def library(self) -> Library: ...

    def invalidate(self) -> None: ...


def library_index(db: sqlite3.Connection, sched: Scheduler) -> dict[str, LibraryItem]:
    items: dict[str, LibraryItem] = {}
    for r in db.execute(
        "SELECT s.id, s.title, MIN(e.air_date) AS first, MAX(e.air_date) AS last,"
        " COUNT(e.id) AS known"
        " FROM shows s LEFT JOIN episodes e ON e.show_id = s.id GROUP BY s.id"
    ):
        # Playable is what load_library loaded: a normalized file, or one already under the
        # media root with a runtime, and not a sample shorter than half its runtime. Raw
        # sources from `ingest scan` count once normalize has run.
        playable = sched.programs(r["id"])
        dated = [p.air_date for p in playable if p.air_date]
        first = min(dated) if dated else (date.fromisoformat(r["first"]) if r["first"] else None)
        last = max(dated) if dated else (date.fromisoformat(r["last"]) if r["last"] else None)
        items[r["id"]] = LibraryItem(
            id=r["id"],
            title=r["title"],
            kind="show",
            first_air=first,
            last_air=last,
            episodes=len(playable),
            known=r["known"],
        )
    for r in db.execute("SELECT id, title, premiere_date FROM movies"):
        premiere = date.fromisoformat(r["premiere_date"]) if r["premiere_date"] else None
        on_disk = 1 if r["id"] in sched.library.movies else 0
        items.setdefault(
            r["id"],
            LibraryItem(r["id"], r["title"], "movie", premiere, premiere, on_disk, 1),
        )
    # Carried titles the database doesn't know yet: seasons of another series (overrides.yaml
    # `part_of`, kept under the series' id), or nothing on disk anywhere, such as shows the
    # channel builder placed. Listed so the rig's `ingest acquire` sees them and /admin can
    # carry them too.
    for c in sched.catalog.carriage.carriage:
        if c.show in items:
            continue
        playable = sched.programs(c.show) if c.kind == "show" else ()
        dated = [p.air_date for p in playable if p.air_date]
        first, last = (min(dated), max(dated)) if dated else (None, None)
        items[c.show] = LibraryItem(
            c.show, c.title, c.kind, first, last, len(playable), len(playable)
        )
    return items


def router(
    schedules: Schedules,
    clock: Callable[[], datetime],
    db_path: Path,
    seed: int,
) -> APIRouter:
    api = APIRouter(prefix="/admin", tags=["admin"])
    migrated: set[tuple[int, int]] = set()  # (device, inode) of database files migrated

    @contextmanager
    def database() -> Iterator[sqlite3.Connection]:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = connect(db_path)
        try:
            # Migrate once per database file, not on every request: reads stay reads.
            st = db_path.stat()
            if (st.st_dev, st.st_ino) not in migrated:
                migrate(db)
                migrated.add((st.st_dev, st.st_ino))
            yield db
        finally:
            db.close()

    def state(db: sqlite3.Connection) -> AdminState:
        rev = store.head(db)
        version = apply_overlay(schedules.base, rev.overlay).version
        return AdminState(revision=rev.id, catalog_version=version, can_undo=rev.can_undo)

    def week_day(sched: Scheduler, req: DraftRequest) -> date:
        return tv.resolve_date(clock(), sched, req.year, req.date)

    def run_check(db: sqlite3.Connection, req: DraftRequest, channel_id: str) -> Checked:
        sched = schedules()
        head = store.head(db).overlay
        index = library_index(db, sched)
        templates: list[Template] = []
        notes: list[CheckIssue] = []
        if req.channel_id:
            # Day-parts the channel builder made stay while they still fit the edit.
            mine = [t for t in head.templates if t.channel == req.channel_id]
            templates, notes = keep_templates(mine, req.draft)
            # Shows it put on the channel before they reached the library stay editable;
            # the checks warn they have no playable files yet.
            for c in head.carriage:
                if c.channel == req.channel_id and c.show not in index:
                    index[c.show] = LibraryItem(c.show, c.title, c.kind, None, None, 0, 0)
            head = without_channel(head, req.channel_id)
        checked = check_draft(
            schedules.base,
            head,
            channel_id,
            req.draft,
            index,
            schedules.library,
            seed,
            week_day(sched, req),
            templates,
        )
        if not notes:
            return checked
        result = checked.result.model_copy(update={"warnings": [*checked.result.warnings, *notes]})
        return replace(checked, result=result)

    def custom_or_404(db: sqlite3.Connection, channel_id: str) -> Overlay:
        overlay = store.head(db).overlay
        if not any(c.id == channel_id for c in overlay.channels):
            sched = schedules()
            if any(c.id == channel_id for c in sched.catalog.channels.channels):
                raise HTTPException(403, f"{channel_id} is defined in the catalog YAML")
            raise HTTPException(404, f"no channel {channel_id}")
        return overlay

    @api.get("/channels", name="adminListChannels")
    def list_channels() -> AdminChannelList:
        """Every channel, YAML and custom, by number."""
        sched = schedules()
        with database() as db:
            current = state(db)
        custom = {c.id for c in sched.catalog.channels.channels if c.id.startswith(CUSTOM_PREFIX)}
        counts: dict[str, set[str]] = {}
        for c in sched.catalog.carriage.carriage:
            counts.setdefault(c.channel, set()).add(c.show)
        rows = []
        for ch in sched.catalog.channels.channels:
            ident = sorted(ch.identity, key=lambda i: i.from_)[-1]
            rows.append(
                AdminChannelRow(
                    id=ch.id,
                    name=ch.name,
                    number=ident.number,
                    call_sign=ident.call_sign,
                    type=ch.type,
                    library=ch.library,
                    launch=ch.launch,
                    custom=ch.id in custom,
                    shows=len(counts.get(ch.id, ())),
                )
            )
        rows.sort(key=lambda r: (r.number, r.name))
        return AdminChannelList(state=current, channels=rows)

    def channel_show(
        sched: Scheduler, overlay: Overlay, channel_id: str, show: str, kind: str
    ) -> AdminChannelShow:
        windows = [
            c for c in sched.catalog.carriage.carriage if c.channel == channel_id and c.show == show
        ]
        ends = [c.until for c in windows]
        until = None if None in ends else max(e for e in ends if e is not None)
        if kind == "movie":
            real = sched.library.movies.get(show)
            mine = sched.movie(show, channel_id)
            debut = real.air_date if real else None
            first = last = mine.air_date if mine else None
        else:
            real_dates = [p.air_date for p in sched.programs(show) if p.air_date]
            dated = [p.air_date for p in sched.programs(show, channel_id) if p.air_date]
            debut = min(real_dates) if real_dates else None
            first, last = (min(dated), max(dated)) if dated else (None, None)
        premiere = sched.premiere(channel_id, show)
        source: Literal["admin", "catalog"] | None = None
        if premiere is not None:
            mine_admin = any(p.show == show and p.channel == channel_id for p in overlay.premieres)
            source = "admin" if mine_admin else "catalog"
        return AdminChannelShow(
            show=show,
            kind="movie" if kind == "movie" else "show",
            debut=debut,
            premiere=premiere,
            premiere_source=source,
            first_air=first,
            last_air=last,
            on_air_until=until,
            airs=first is not None and first <= (until or LAST_DAY),
        )

    @api.get("/channels/{channel_id}", name="adminGetChannel", responses={404: {}})
    def get_channel(channel_id: str) -> AdminChannel:
        sched = schedules()
        try:
            channel = sched.catalog.channel(channel_id)
        except KeyError as e:
            raise HTTPException(404, f"no channel {channel_id}") from e
        with database() as db:
            overlay = store.head(db).overlay
        draft = to_draft(
            channel, sched.catalog.carriage.carriage, sched.catalog.synthetic.synthetic
        )
        shows = [channel_show(sched, overlay, channel_id, s.show, s.kind) for s in draft.shows]
        return AdminChannel(
            id=channel_id, custom=channel_id.startswith(CUSTOM_PREFIX), draft=draft, shows=shows
        )

    @api.put(
        "/channels/{channel_id}/premieres/{show}",
        name="adminSetPremiere",
        responses={404: {}, 409: {"model": CheckResult, "description": "Not saved"}},
    )
    def set_premiere(channel_id: str, show: str, req: PremiereRequest) -> PremiereResult:
        """Set or clear the pretend premiere of one carried show on one channel (YAML or
        custom). It changes inputs only: an overlay revision, undoable like any save. Other
        channels keep the show's real dates."""
        sched = schedules()
        try:
            channel = sched.catalog.channel(channel_id)
        except KeyError as e:
            raise HTTPException(404, f"no channel {channel_id}") from e
        windows = [
            c for c in sched.catalog.carriage.carriage if c.channel == channel_id and c.show == show
        ]
        if not windows:
            raise HTTPException(404, f"{show} is not carried on {channel_id}")

        def refuse(message: str) -> JSONResponse:
            issue = CheckIssue(field="premiere", message=message)
            result = CheckResult(ok=False, errors=[issue], warnings=[], catalog_version=None)
            return JSONResponse(status_code=409, content=result.model_dump(mode="json"))

        with database() as db:
            overlay = store.head(db).overlay
            title = windows[0].title
            warnings: list[CheckIssue] = []
            mine = any(p.show == show and p.channel == channel_id for p in overlay.premieres)
            if req.premiere is None:
                if not mine and sched.premiere(channel_id, show) is not None:
                    return refuse(  # type: ignore[return-value]
                        "This pretend premiere is set in the catalog files, so it can't be"
                        " cleared here."
                    )
            else:
                current = channel_show(sched, overlay, channel_id, show, windows[0].kind)
                dial = None if channel.library else channel.launch
                starts = min(c.from_ for c in windows)
                if not channel.library:
                    dial = max(starts, dial) if dial else starts
                errors, notes = premiere_issues(
                    title, req.premiere, current.debut, dial, current.on_air_until
                )
                if errors:
                    return refuse(errors[0])  # type: ignore[return-value]
                warnings = [CheckIssue(field="premiere", message=m) for m in notes]
            updated = with_premiere(overlay, show, channel_id, req.premiere)
            problems = validate(apply_overlay(schedules.base, updated))
            if problems:
                return refuse(problems[0])  # type: ignore[return-value]
            verb = "Cleared" if req.premiere is None else "Set"
            store.save(db, updated, f"premiere {show} on {channel_id}", clock())
            schedules.invalidate()
            fresh = schedules()
            row = channel_show(fresh, updated, channel_id, show, windows[0].kind)
            return PremiereResult(
                message=f"{verb} the pretend premiere of {title}",
                show=row,
                warnings=warnings,
                state=state(db),
            )

    @api.get("/library", name="adminLibrary")
    def get_library(
        q: Annotated[str, Query(max_length=100, description="Title search")] = "",
    ) -> LibraryList:
        """Every show and movie in the library, including ones no channel carries."""
        sched = schedules()
        with database() as db:
            index = library_index(db, sched)
        carriers: dict[str, dict[str, LibraryChannel]] = {}
        for c in sched.catalog.carriage.carriage:
            ch = sched.catalog.channel(c.channel)
            ident = sorted(ch.identity, key=lambda i: i.from_)[-1]
            carriers.setdefault(c.show, {})[ch.id] = LibraryChannel(
                id=ch.id, name=ch.name, number=ident.number
            )
        needle = q.strip().lower()
        lineup = {o.show: o for o in sched.catalog.overrides.shows}
        shifted = {p.show for p in sched.catalog.synthetic.synthetic} | {
            o.show for o in sched.catalog.overrides.shows if o.us_debut
        }
        items = [
            LibraryEntry(
                id=item.id,
                title=item.title,
                kind=item.kind,
                first_year=item.first_air.year if item.first_air else None,
                last_year=item.last_air.year if item.last_air else None,
                debut=item.first_air,
                episodes=item.episodes,
                channels=sorted(carriers.get(item.id, {}).values(), key=lambda c: c.number),
                tmdb_id=lineup[item.id].tmdb_id if item.id in lineup else None,
                year=lineup[item.id].year if item.id in lineup else None,
                shifted=item.id in shifted,
            )
            for item in index.values()
            if needle in item.title.lower()
        ]
        items.sort(key=lambda e: (bool(e.channels), e.title.lower()))
        return LibraryList(items=items)

    @api.post("/check", name="adminCheck")
    def check(req: DraftRequest) -> CheckResult:
        """Run the save checks on a draft without saving it."""
        with database() as db:
            return run_check(db, req, req.channel_id or CUSTOM_PREFIX + "draft").result

    @api.post("/preview", name="adminPreview")
    def preview(req: DraftRequest) -> Preview:
        """A week (Sunday to Saturday) of the draft channel's guide, with the draft applied in
        memory only. The days are empty when the checks fail."""
        channel_id = req.channel_id or CUSTOM_PREFIX + "draft"
        with database() as db:
            checked = run_check(db, req, channel_id)
        if checked.scheduler is None or checked.channel is None:
            return Preview(check=checked.result, channel=None, days=[])
        sched = checked.scheduler  # the same engine the checks just ran, days memoized
        anchor = week_day(sched, req)
        days = []
        for d in preview_week(anchor):
            on_air = sched.on_air(checked.channel, d)
            slots = []
            if on_air:
                try:
                    slots = [
                        tv.GuideSlot.model_validate(tv.guide_slot(sched, s))
                        for s in sched.day(channel_id, d)
                    ]
                except ChannelOffAir:
                    on_air = False
            days.append(PreviewDay(date=d, weekday=d.strftime("%A"), on_air=on_air, slots=slots))
        return Preview(
            check=checked.result, channel=tv.info(sched, checked.channel, anchor), days=days
        )

    def save(db: sqlite3.Connection, req: DraftRequest, channel_id: str, verb: str) -> object:
        checked = run_check(db, req, channel_id)
        if checked.overlay is None:
            return JSONResponse(status_code=409, content=checked.result.model_dump(mode="json"))
        name = req.draft.name.strip()
        store.save(db, checked.overlay, f"{verb} {name}", clock())
        schedules.invalidate()
        return SaveResult(
            channel_id=channel_id,
            message=f"{'Saved' if verb == 'update' else 'Created'} {name}",
            check=checked.result,
            state=state(db),
        )

    save_responses: dict[int | str, dict[str, object]] = {
        409: {"model": CheckResult, "description": "The checks failed; nothing was saved"}
    }

    @api.post("/channels", name="adminCreateChannel", responses=save_responses)
    def create_channel(req: DraftRequest) -> SaveResult:
        """Check and save a new custom channel. The catalog version changes."""
        with database() as db:
            taken = {c.id for c in schedules().catalog.channels.channels}
            return save(db, req.model_copy(update={"channel_id": None}),
                        new_channel_id(req.draft.name, taken), "create")  # type: ignore[return-value]  # fmt: skip

    @api.put(
        "/channels/{channel_id}",
        name="adminUpdateChannel",
        responses={**save_responses, 403: {}, 404: {}},
    )
    def update_channel(channel_id: str, req: DraftRequest) -> SaveResult:
        """Check and save changes to a custom channel."""
        with database() as db:
            custom_or_404(db, channel_id)
            return save(db, req.model_copy(update={"channel_id": channel_id}),
                        channel_id, "update")  # type: ignore[return-value]  # fmt: skip

    @api.delete("/channels/{channel_id}", name="adminDeleteChannel", responses={403: {}, 404: {}})
    def delete_channel(channel_id: str) -> ChangeResult:
        """Remove a custom channel. The YAML files are never touched."""
        with database() as db:
            overlay = custom_or_404(db, channel_id)
            channel = next(c for c in overlay.channels if c.id == channel_id)
            number = sorted(channel.identity, key=lambda i: i.from_)[-1].number
            store.save(db, without_channel(overlay, channel_id), f"delete {channel.name}", clock())
            schedules.invalidate()
            return ChangeResult(message=f"Deleted {number} {channel.name}", state=state(db))

    @api.post("/undo", name="adminUndo", responses={409: {"description": "Nothing to undo"}})
    def undo() -> ChangeResult:
        """Revert the last save or delete: the previous inputs, so the same catalog version
        and the same schedules."""
        with database() as db:
            try:
                store.undo(db, clock())
            except store.NothingToUndo as e:
                raise HTTPException(409, "nothing to undo") from e
            schedules.invalidate()
            return ChangeResult(message="Restored", state=state(db))

    @api.get("/state", name="adminState")
    def get_state() -> AdminState:
        with database() as db:
            return state(db)

    return api
