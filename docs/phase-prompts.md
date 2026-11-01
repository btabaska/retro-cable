# Phase prompts for Claude Code

Paste one prompt per Claude Code session. Each phase may take several PRs; split as needed and keep each PR reviewable.

## Phase 1 — Skeleton

```
Read CLAUDE.md, docs/STATUS.md and docs/plan.md (sections "Git, CI/CD and releases",
"Local build and test on your Mac", "Hosting, networking and security").
Implement Phase 1 — Skeleton:

- Repo layout and Makefile with every target listed in CLAUDE.md.
- server/: FastAPI app with /health and /version, OpenAPI exported to server/openapi.json,
  pytest, ruff, mypy --strict, managed with uv.
- web/: Vite + React + TypeScript (strict). A 1920x1080 "hello" cable-box screen scaled to
  the window, styled from design/tokens.json, with arrow-key focus moving between three
  placeholder guide cells. API client generated from server/openapi.json. Vitest unit test and
  a Playwright test that loads the page and shows the server /version.
- tools/fixtures: `make fixtures` uses ffmpeg to write 3 fake shows x 3 episodes (60 s each)
  with show, episode and running timecode burned in, plus 2 bumpers, into .fixtures/.
- ops/: compose.yml (api + web static files behind Caddy), compose.test.yml (with fixtures),
  caddy/retro-cable.caddy (LAN-only matcher for private IP ranges), and deploy.sh, rollback.sh,
  smoke.sh with set -euo pipefail.
- .github/workflows: server.yml and web.yml on every PR (cloud runners); local-test.yml on
  runs-on [self-hosted, macOS, retro-cable], only for PRs labeled local-test, uploading
  Playwright traces and screenshots; release.yml on main (GHCR image + release-please).
- docs/specs/: split docs/plan.md into scheduler.md, catalog.md, api.md, web.md, ops.md
  without changing meaning.

Write failing tests first. Open one PR labeled local-test.
```

## Phase 2 — Catalog and measurements

```
Read CLAUDE.md, docs/specs/catalog.md and catalog/*.yaml.
Implement Phase 2: SQLite catalog schema and migrations; YAML loaders with JSON Schema
validation for channels, holidays, carriage, synthetic dates and overrides; a Plex importer
(tested against recorded API responses in server/tests/fixtures/plex/); a TMDB gap filler
behind an interface; and `make inventory` writing an episodes-per-channel-per-year and
gaps report. Tests first. One PR per component is fine.
```

## Phase 3 — Scheduler

```
Read CLAUDE.md and docs/specs/scheduler.md.
Implement Phase 3: the scheduler as a pure package with an injected clock; day-part
templates; eligibility by US debut or synthetic date; air-order cursors computed, not stored;
back-to-back programming with one bumper between items; holiday windows; GET /guide.
Write Hypothesis property tests for every invariant in CLAUDE.md and golden-day snapshot
tests for Oct 31 1998, Dec 24 2003, Sep 2 2001 and Jan 1 2002, all on the fixture catalog.
```

## Phase 4 — Media and tuning

```
Read CLAUDE.md, docs/specs/api.md and docs/specs/ops.md.
Implement Phase 4: the ffmpeg normalize job (H.264 + AAC MP4, 480p, faststart, 4:3 kept),
HMAC-signed expiring media URLs served by Caddy file_server, and GET /tune/{channel}
returning the current item, offset and next items. Playwright test: tune a fixture channel and
assert the video's currentTime is within 1 s of the returned offset. Label local-test.
```

## Phase 5 — Live TV in the browser

```
Read CLAUDE.md and docs/specs/web.md; match the Tuned screen in docs/mockups/.
Implement Phase 5 in web/: full-screen playback at offset, channel up/down (arrow keys),
last channel, the info banner with progress and "next", 4:3 pillarbox, and an era-style
no-signal screen. Report tune latency as a telemetry event. Playwright tests for each.
Label local-test.
```

## Phase 6 — Guide and year picker

```
Read CLAUDE.md and docs/specs/web.md; match the Guide and Year screens in docs/mockups/.
Implement Phase 6 in web/: the guide grid with cells sized to real run times, details panel,
live picture-in-picture, power-hour band placeholder, 12 h scroll, spatial focus; and the
year picker that retunes instantly. Playwright tests, including a screenshot check that the
guide matches what /tune plays. Label local-test.
```

## Phase 7 — Feel

```
Read CLAUDE.md and docs/specs/scheduler.md.
Implement Phase 7: bumper and promo reels from catalog bumpers, holiday windows from
catalog/holidays.yaml with tagged episodes and specials, movie items, NEW badges in the guide.
Golden-day test: Oct 31 1998 on fixtures airs only holiday-tagged items where tags exist.
```

## Phase 8 — Operations

```
Read CLAUDE.md and docs/specs/ops.md.
Implement Phase 8: OpenTelemetry export, Prometheus, Loki and Grafana in compose, dashboards
as code, Hermes skill files for deploy, rollback, ingest, patching, backups and the weekly
report, and a backup restore test. No secrets in the repo.
```

## Phases 9–12

Write these prompts when Phase 8 is signed off: tvOS app, admin panel and custom channels,
local AI channel builder and station manager, polish.
