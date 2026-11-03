# Two images from one file:
#   api     (default, the last stage): the server and media-index; no ffmpeg
#   worker  (--target worker): the same plus ffmpeg/ffprobe for scan and normalize jobs
#           (ops/compose.yml `worker`, the rig's ops/rig/compose.yml)
# Debian's ffmpeg and its libraries are over half the old shared image, and the api never
# runs it, so only the worker carries it. uv, its cache and the build venv stay in `build`.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# The runtime user and its data dir (a fresh named volume copies /data's owner).
FROM python:3.12-slim AS base
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir /data && chown app:app /data

FROM base AS worker
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv
ARG RC_GIT_SHA=dev
ENV PATH=/app/.venv/bin:$PATH RC_GIT_SHA=$RC_GIT_SHA
USER app
CMD ["python", "-m", "retro_cable.ingest", "--help"]

FROM base AS api
COPY --from=build /app/.venv /app/.venv
ARG RC_GIT_SHA=dev
ENV PATH=/app/.venv/bin:$PATH RC_GIT_SHA=$RC_GIT_SHA
USER app
EXPOSE 8000
CMD ["uvicorn", "retro_cable.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
