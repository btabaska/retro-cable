"""FastAPI application factory."""

import logging
import os
import secrets
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

from retro_cable import __version__, media, tv
from retro_cable.admin import router as admin
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


@dataclass(frozen=True)
class Settings:
    catalog_dir: Path
    db_path: Path
    media_dir: Path
    media_secret: str = field(repr=False)
    seed: int = 0

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
        )


class _Schedules:
    """The current Scheduler, rebuilt when the catalog files, the database or the admin
    overlay change. The effective catalog is the YAML plus the head admin overlay."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stamp: tuple[float, ...] | None = None
        self._scheduler: Scheduler | None = None
        self._base: Catalog | None = None
        self._library = Library()

    def _mtimes(self) -> tuple[float, ...]:
        paths = sorted(self.settings.catalog_dir.glob("*.yaml"))
        db = self.settings.db_path
        # Commits from a long-running ingest sit in the WAL until a checkpoint.
        paths += [db, db.with_name(db.name + "-wal")]
        return tuple(p.stat().st_mtime if p.exists() else 0.0 for p in paths)

    def invalidate(self) -> None:
        self._scheduler = None

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
        stamp = self._mtimes()
        if self._scheduler is None or stamp != self._stamp:
            catalog = load_catalog(self.settings.catalog_dir)
            library = Library()
            overlay = Overlay()
            if self.settings.db_path.exists():
                db = connect(self.settings.db_path)
                try:
                    migrate(db)
                    library = load_library(db)
                    overlay = store.head(db).overlay
                finally:
                    db.close()
            self._base, self._library = catalog, library
            self._scheduler = Scheduler(
                apply_overlay(catalog, overlay), library, seed=self.settings.seed
            )
            self._stamp = stamp
        return self._scheduler


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

    @app.get("/health", name="getHealth")
    def get_health() -> Health:
        return Health(status="ok")

    @app.get("/version", name="getVersion")
    def get_version() -> Version:
        return Version(version=__version__, commit=os.environ.get("RC_GIT_SHA", "dev"))

    def sign(path: str) -> str:
        return media.sign(config.media_secret, path, now=time.time())

    app.include_router(tv.router(schedules, clock, sign))
    app.include_router(admin.router(schedules, clock, config.db_path, config.seed))

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
