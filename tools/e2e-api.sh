#!/usr/bin/env bash
# Start the API on demo data for Playwright (web/playwright.config.ts runs this).
# Reuses the demo feeds from `make demo-db` when present; otherwise writes tiny ones.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .fixtures/_demo/manifest.json ]]; then
  uv run --project server python tools/fixtures/generate.py --out .fixtures --demo --demo-scale 0.02
fi
mkdir -p data
(cd server && uv run python -m retro_cable.demo --db ../data/e2e.db --catalog ../catalog --media ../.fixtures)
cd server
RC_DB=../data/e2e.db RC_MEDIA_DIR=../.fixtures exec uv run uvicorn retro_cable.app:app \
  --host 127.0.0.1 --port 8000 --no-access-log
