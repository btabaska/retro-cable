"""The /admin API. LAN-only like the rest of the app, with no auth in M1.

Every write changes inputs only: it appends an overlay revision (store.py), which the
schedules merge into the catalog and its version. Nothing here writes a schedule.
"""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    CheckResult,
    LibraryItem,
    check_draft,
    new_channel_id,
    preview_week,
    to_draft,
)
from retro_cable.catalog import Catalog
from retro_cable.catalog.overlay import (
    CUSTOM_PREFIX,
    Overlay,
    apply_overlay,
    without_channel,
)
from retro_cable.db import connect, migrate
from retro_cable.scheduler import ChannelOffAir, Library, Scheduler

DEFAULT_YEAR = 2000
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


class AdminChannel(BaseModel):
    id: str
    custom: bool
    draft: ChannelDraft


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
    episodes: int = Field(description="Episodes (or the movie) on disk")
    channels: list[LibraryChannel] = Field(description="Channels that carry it, in any year")


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
        " COUNT(e.id) AS known,"
        # Playable as load_library sees it: a normalized file, or one already under the media
        # root with a runtime. Raw sources from `ingest scan` count once normalize has run.
        " SUM(CASE WHEN e.media_path IS NOT NULL AND e.media_seconds > 0 THEN 1"
        "  WHEN e.media_path IS NULL AND e.file_path NOT LIKE '/%' AND e.runtime_seconds > 0"
        "  THEN 1 ELSE 0 END)"
        " AS on_disk FROM shows s LEFT JOIN episodes e ON e.show_id = s.id GROUP BY s.id"
    ):
        dated = [p.air_date for p in sched.programs(r["id"]) if p.air_date]
        first = min(dated) if dated else (date.fromisoformat(r["first"]) if r["first"] else None)
        last = max(dated) if dated else (date.fromisoformat(r["last"]) if r["last"] else None)
        items[r["id"]] = LibraryItem(
            id=r["id"],
            title=r["title"],
            kind="show",
            first_air=first,
            last_air=last,
            episodes=r["on_disk"] or 0,
            known=r["known"],
        )
    for r in db.execute(
        "SELECT id, title, premiere_date, runtime_seconds, file_path, media_path, media_seconds"
        " FROM movies"
    ):
        premiere = date.fromisoformat(r["premiere_date"]) if r["premiere_date"] else None
        if r["media_path"]:
            on_disk = 1 if (r["media_seconds"] or 0) > 0 else 0
        else:
            relative = r["file_path"] and not r["file_path"].startswith("/")
            on_disk = 1 if relative and (r["runtime_seconds"] or 0) > 0 else 0
        items.setdefault(
            r["id"],
            LibraryItem(r["id"], r["title"], "movie", premiere, premiere, on_disk, 1),
        )
    return items


def router(
    schedules: Schedules,
    clock: Callable[[], datetime],
    db_path: Path,
    seed: int,
) -> APIRouter:
    api = APIRouter(prefix="/admin", tags=["admin"])

    @contextmanager
    def database() -> Iterator[sqlite3.Connection]:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = connect(db_path)
        try:
            migrate(db)
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
        if req.channel_id:
            head = without_channel(head, req.channel_id)
        return check_draft(
            schedules.base,
            head,
            channel_id,
            req.draft,
            library_index(db, sched),
            schedules.library,
            seed,
            week_day(sched, req),
        )

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

    @api.get("/channels/{channel_id}", name="adminGetChannel", responses={404: {}})
    def get_channel(channel_id: str) -> AdminChannel:
        sched = schedules()
        try:
            channel = sched.catalog.channel(channel_id)
        except KeyError as e:
            raise HTTPException(404, f"no channel {channel_id}") from e
        draft = to_draft(channel, sched.catalog.carriage.carriage)
        return AdminChannel(id=channel_id, custom=channel_id.startswith(CUSTOM_PREFIX), draft=draft)

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
        items = [
            LibraryEntry(
                id=item.id,
                title=item.title,
                kind=item.kind,
                first_year=item.first_air.year if item.first_air else None,
                last_year=item.last_air.year if item.last_air else None,
                episodes=item.episodes,
                channels=sorted(carriers.get(item.id, {}).values(), key=lambda c: c.number),
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
