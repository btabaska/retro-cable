"""FastAPI application factory."""

import os

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from retro_cable import __version__


class Health(BaseModel):
    status: str


class Version(BaseModel):
    version: str
    commit: str


def _operation_id(route: APIRoute) -> str:
    # The generated web and tvOS clients use these names, so keep them stable.
    return route.name


def create_app() -> FastAPI:
    app = FastAPI(
        title="Retro Cable",
        version=__version__,
        generate_unique_id_function=_operation_id,
    )

    @app.get("/health", name="getHealth")
    def get_health() -> Health:
        return Health(status="ok")

    @app.get("/version", name="getVersion")
    def get_version() -> Version:
        return Version(version=__version__, commit=os.environ.get("RC_GIT_SHA", "dev"))

    return app


app = create_app()
