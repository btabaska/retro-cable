FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
# ffmpeg/ffprobe for the worker's normalize job (the worker runs this same image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir /data && chown app:app /data
COPY --from=build /app/.venv /app/.venv
ARG RC_GIT_SHA=dev
ENV PATH=/app/.venv/bin:$PATH RC_GIT_SHA=$RC_GIT_SHA
USER app
EXPOSE 8000
CMD ["uvicorn", "retro_cable.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
