"""FastAPI application factory."""

import logging
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from retro_cable import __version__, media, tv, watch
from retro_cable.admin import media as admin_media
from retro_cable.admin import router as admin
from retro_cable.admin import station as admin_station
from retro_cable.admin import store
from retro_cable.catalog import Catalog, load_catalog
from retro_cable.catalog.overlay import Overlay, apply_overlay
from retro_cable.db import connect, migrate
from retro_cable.scheduler import Library, Scheduler, load_library

log = logging.getLogger(__name__)
REPO_CATALOG = Path(__file__).resolve().parents[3] / "catalog"


class Health(BaseModel):
    status: str


class Version(BaseModel):
    version: str
    commit: str


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    catalog_dir: Path
    db_path: Path
    media_dir: Path
    media_secret: str = field(repr=False)
    seed: int = 0
    # Schedule files shorter than half their expected runtime (samples, trial encodes).
    # Off by default: a 2-minute clip must never air as a whole episode.
    allow_short_media: bool = False

    @staticmethod
    def from_env() -> "Settings":
        data = Path(os.environ.get("RC_DATA_DIR", "data"))
        secret = os.environ.get("RC_MEDIA_SECRET")
        if not secret:
            log.warning("RC_MEDIA_SECRET is unset; media URLs last until the API restarts")
            secret = secrets.token_hex(32)
        return Settings(
            catalog_dir=Path(os.environ.get("RC_CATALOG_DIR", REPO_CATALOG)),
            db_path=Path(os.environ.get("RC_DB", data / "retro-cable.db")),
            media_dir=Path(os.environ.get("RC_MEDIA_DIR", data / "media")),
            media_secret=secret,
            seed=int(os.environ.get("RC_SEED", "0")),
            allow_short_media=_flag(os.environ.get("RC_ALLOW_SHORT_MEDIA", "")),
        )


class _Schedules:
    """The current Scheduler, rebuilt when the catalog files, the database or the admin
    overlay change. The effective catalog is the YAML plus the head admin overlay.

    The database part of the cache stamp is SQLite's `PRAGMA data_version` on one
    long-lived connection, which changes only when another connection commits. File
    mtimes are not used for the database: opening and closing connections (WAL checkpoints,
    the -wal file coming and going) changed them on plain reads, so every admin read
    rebuilt the Scheduler and stalled the next TV request. A rebuild runs under a lock, so
    concurrent requests after a change share one rebuild."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stamp: tuple[object, ...] | None = None
        self._scheduler: Scheduler | None = None
        self._base: Catalog | None = None
        self._library = Library()
        self._lock = threading.RLock()
        self._watch: sqlite3.Connection | None = None
        self._watch_file: tuple[int, int] | None = None

    def _db_stamp(self) -> tuple[object, ...]:
        """(device, inode, data_version) of the database, or () when there is none. Call
        with the lock held."""
        try:
            st = self.settings.db_path.stat()
        except FileNotFoundError:
            self._close_watch()
            return ()
        ident = (st.st_dev, st.st_ino)
        if self._watch is None or ident != self._watch_file:
            self._close_watch()
            self._watch = connect(self.settings.db_path, check_same_thread=False)
            migrate(self._watch)
            self._watch_file = ident
        (version,) = self._watch.execute("PRAGMA data_version").fetchall()[0]
        return (*ident, version)

    def _close_watch(self) -> None:
        if self._watch is not None:
            self._watch.close()
        self._watch, self._watch_file = None, None

    def _stamp_now(self) -> tuple[object, ...]:
        yaml = tuple(
            (p.name, p.stat().st_mtime) for p in sorted(self.settings.catalog_dir.glob("*.yaml"))
        )
        return (yaml, self._db_stamp())

    def invalidate(self) -> None:
        with self._lock:
            self._scheduler = None

    def write(self, fn: Callable[[sqlite3.Connection], None]) -> None:
        """Run a small write that no schedule depends on (a viewing report) on the watched
        connection itself. `data_version` moves only for commits by other connections, so
        the TV's reports every minute never make the next /tune rebuild every schedule."""
        with self._lock:
            if not self.settings.db_path.exists():
                self.settings.db_path.parent.mkdir(parents=True, exist_ok=True)
                connect(self.settings.db_path).close()
            self._db_stamp()  # opens (and migrates) the watched connection if need be
            assert self._watch is not None
            fn(self._watch)

    @property
    def base(self) -> Catalog:
        self()
        assert self._base is not None
        return self._base

    @property
    def library(self) -> Library:
        self()
        return self._library

    def __call__(self) -> Scheduler:
        with self._lock:
            stamp = self._stamp_now()
            if self._scheduler is not None and stamp == self._stamp:
                return self._scheduler
            catalog = load_catalog(self.settings.catalog_dir)
            library = Library()
            overlay = Overlay()
            if self._watch is not None:
                library = load_library(self._watch, allow_short=self.settings.allow_short_media)
                overlay = store.head(self._watch).overlay
            self._base, self._library = catalog, library
            self._scheduler = Scheduler(
                apply_overlay(catalog, overlay), library, seed=self.settings.seed
            )
            self._stamp = stamp
            return self._scheduler


def _data_writable(db_path: Path) -> bool:
    """False when the database, or the folder it lives in, exists but this process can't
    write it (a host data dir owned by another uid). A folder not made yet is fine: the
    first write makes it."""
    folder = db_path.parent
    if db_path.exists() and not os.access(db_path, os.W_OK):
        return False
    return not folder.exists() or os.access(folder, os.W_OK | os.X_OK)


def _operation_id(route: APIRoute) -> str:
    # The generated web and tvOS clients use these names, so keep them stable.
    return route.name


def _real_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None, clock: Callable[[], datetime] = _real_now
) -> FastAPI:
    app = FastAPI(
        title="Retro Cable",
        version=__version__,
        generate_unique_id_function=_operation_id,
    )
    config = settings or Settings.from_env()
    schedules = _Schedules(config)

    @app.get(
        "/health",
        name="getHealth",
        responses={503: {"model": Health, "description": "The data dir is not writable"}},
    )
    def get_health(response: Response) -> Health:
        if not _data_writable(config.db_path):
            response.status_code = 503
            return Health(status="data dir not writable")
        return Health(status="ok")

    @app.get("/version", name="getVersion")
    def get_version() -> Version:
        return Version(version=__version__, commit=os.environ.get("RC_GIT_SHA", "dev"))

    def sign(path: str) -> str:
        return media.sign(config.media_secret, path, now=time.time())

    app.include_router(tv.router(schedules, clock, sign))
    app.include_router(admin.router(schedules, clock, config.db_path, config.seed))
    app.include_router(admin_media.router(config.media_dir, clock))
    app.include_router(admin_station.router(config.db_path))
    app.include_router(watch.router(schedules, clock))

    @app.get("/media/{path:path}", name="getMedia", include_in_schema=False)
    def get_media(
        path: str, exp: Annotated[int, Query()], sig: Annotated[str, Query()]
    ) -> FileResponse:
        # Dev and test server for media; behind Caddy the same check guards file_server.
        if not media.verify(config.media_secret, path, exp, sig, now=time.time()):
            raise HTTPException(403, "bad or expired media signature")
        target = media.resolve(config.media_dir, path)
        if target is None:
            raise HTTPException(404, "no such media")
        return FileResponse(target)

    @app.api_route(
        "/media-auth", methods=["GET", "HEAD"], name="mediaAuth", include_in_schema=False
    )
    def media_auth(
        x_forwarded_uri: Annotated[str | None, Header()] = None,
    ) -> Response:
        # Caddy's forward_auth (web/Caddyfile) asks here before file_server answers /media/*.
        if x_forwarded_uri is None or not media.verify_uri(
            config.media_secret, x_forwarded_uri, now=time.time()
        ):
            return Response(status_code=403)
        return Response(status_code=204)

    return app


app = create_app()
