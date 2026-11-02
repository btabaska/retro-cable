# Status

**Current phase:** Milestone M1 — phases 3–7 in review (scheduler through Feel), playable on demo media with `make demo`

## Phase 7 — Feel (in review, stacked on Phase 5)

- [x] Holiday calendar (`catalog/holidays.yaml`), windows and seasons, per-channel modes
- [x] Holiday tags: overrides, `tagged` titles and a keyword tagger; untagged specials in the inventory
- [x] Tagged episodes preferred in the window; holiday movies and specials injected into prime; seasonal specials kept to their season
- [x] Bumpers between programs, NEW badges, holiday kicker and badges in the guide and banner; bumper slate when a bumper has no media
- [x] Golden day Oct 31 1998 (Halloween episodes and Halloweentown) and the Hypothesis property "holiday windows pull only tagged items"
- [ ] Brandon's check: Oct 31 1998 feels like Halloween 1998

## Phases 3–6 — Scheduler through guide (in review, stacked on Phase 2)

- [x] Scheduler engine with Hypothesis invariants; `/channels`, `/guide`, `/tune` (Phase 3)
- [x] Signed media URLs and `/media` in dev; demo feeds and demo database (`make demo`) (Phase 4)
- [x] Live TV: playback at offset, channel up/down, last channel, number entry, info banner (Phase 5)
- [x] Guide (true run-time cells, PiP) and year picker (Phase 6)
- [ ] Normalize job (ffmpeg) for real media; Caddy signature check on `/media/*`
- [ ] Brandon's couch check: flipping channels feels like cable; the guide matches what plays

## Phase 2 — Catalog and measurements (in review)

- [x] Catalog YAML models, JSON Schemas, validation, SQLite migrations (#4)
- [x] Plex importer and TMDB gap filler (#5)
- [x] `make inventory` (#6)
- [ ] First real import: Plex on the NAS was down on 2026-11-01, so the dry run is pending
- [ ] Done when the report lists episodes per channel and year, plus gaps, from real data

## Phase 1 — Skeleton

- [x] Repo layout, Makefile, server (`/health`, `/version`), web "hello" screen, fixture generator, ops files, workflows
- [x] `docs/specs/` split from `docs/plan.md`
- [ ] Hermes deploys it (needs the homelab host)
- [ ] Mac runner posts its first `local-test` report

## Phase 0 — Permits (Brandon)

- [x] Private repo `btabaska/retro-cable` created
- [ ] Repo connected to Claude Code on the web
- [ ] Claude Code GitHub app installed (for `@claude` mentions on PRs)
- [ ] Mac registered as a self-hosted runner with labels `self-hosted, macOS, retro-cable`
- [ ] Hermes Agent on the Mac (build and test), connected to LiteLLM and Telegram/Signal
- [ ] Hermes Agent on the homelab host (operations)
- [ ] Split-DNS record for `tv.home.tabaska.us`
- [ ] Caddyfile `import` line for `ops/caddy/retro-cable.caddy`

## Open assumptions

See "Assumptions to confirm" in `docs/plan.md`. Still blocking: which homelab host runs the stack.
