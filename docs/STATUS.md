# Status

**Current phase:** Milestone M2: real media on the mini. M1 was approved by Brandon on Nov 2 2026. Phases 3–7 and 10 (part) are still in review as a stacked set of PRs; M2 is #16 (`m2/integration` = #14 + #15 + #3).

## Milestone M2: real library, debut rule everywhere, LAN deploy (in review, #16)

- [x] Debut rule on every channel, including library and custom ones; `library: true` only keeps a channel on the dial before its launch (#15)
- [x] Pretend premieres per (show, channel) in `/admin`: set, edit and clear them; the transplant mark and NEW follow the shifted dates (#15)
- [x] M1 rough edges: Cartoon Network evenings, Disney Movies rotation, focus return, errors, borders, chevrons, 360 px chips, narrow cells, holiday colour (#15)
- [x] Rig worker (`ops/rig/`): GPU-aware encoder (NVENC only when the GPU is idle and ComfyUI's queue is empty, otherwise x264 at low priority), priority order, resumable, manifest (#14)
- [x] Mini: read-only automount of `//192.168.10.4/tv/.retro-cable` at `/mnt/nas-retro-cable`; the `media-index` service records new encodes from `manifest.json` every 5 minutes, with no redeploy (#14)
- [x] Deployed to the mini with `ops/deploy.sh` (build mode, `m2/integration`), LAN only at https://tv.home.tabaska.us through the homelab Caddy; the web port stays on loopback. Checked on Nov 2 from the Mac with Playwright (WebKit and Chromium): power on in 1995, KTVU plays a converted episode at the tune offset, the banner hides, and `/admin` loads; the next program on KTVU, Cartoon Network and Boomerang serves a 206 byte range
- [ ] Conversion of the real library: running on the rig since Nov 2. Carried shows first (13 episodes per show per turn, earliest year first), then uncarried ones; about 50–80 s per episode, so roughly 2 days for the 2,980 carried episodes (about Nov 4–5) and about 6 days for all 8,414 (about Nov 8). 110 were done at 21:10 UTC on Nov 2; the `/admin` Media tab shows live progress and an ETA
- Real episodes on the dial right now (Nov 2, 21:10 UTC): KTVU (The Simpsons 1990–2010; Batman: The Animated Series 1993–95; Animaniacs 1994–95), Cartoon Network and Boomerang (Scooby-Doo 1993–2010; on Cartoon Network also Johnny Bravo, Ed, Edd n Eddy, Courage from 1998–2000), Comedy Central (South Park 1998–2010), Toonami (Batman, 2000). Best years to try: 1995 and 1999–2001. Other channels show Off Air until their shows convert
- [x] `/admin` Media tab backed by `GET /api/admin/media/progress`, read from the `progress.json` the rig worker keeps on the NAS (#14). Encodes count only when whole (video, audio, length within 2% of the source); `/health` and `media-index` now fail a deploy on an unwritable data dir; the web container rejects non-private clients itself (#14)
- [ ] Plex import and TMDB fill: waiting on `PLEX_URL`, `PLEX_TOKEN` and a TMDB key in the mini's `ops/.env` (the code reads `TMDB_TOKEN`). Until then air dates are unknown, so the library comes from the disk scan
- [ ] Brandon's check: open the TV from a LAN device; real episodes play on their channels, and at least three channels tune and play past a program boundary

Waiting on Brandon: the show lineup (`catalog/carriage.yaml` is unchanged), the Plex/TMDB keys, and the M2 questions in #16 (library channels keep their shows; weekly cadence for undated episodes after a pretend premiere; label and colours; whether uncarried shows convert too; NVENC bitrate).

Checks on #16 (`ae9ce4c`): server, web and local-test green; `make lint` and `make test` (server 397, tools 9, ops 44 + 2 skipped, web 79, e2e 8) pass in a clean worktree.

## Phase 10 (part) — Custom channels in /admin (in review, stacked on Phase 7)

- [x] Admin edits stored as an append-only overlay in SQLite, merged into the catalog and its version; YAML untouched
- [x] Admin API: channel list and drafts, library search (incl. shows no channel carries), checks, week preview, create/edit/delete, undo
- [x] `/admin` phone UI: channel list, editor, show picker, week preview, inline check errors, undo toast
- [ ] Brandon's check: make "Brandon's Toons" on the phone, preview, save, tune it, undo
- [ ] Later in Phase 10: holidays editor, editing YAML channels, synthetic premieres, device pairing, nightly YAML export

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
- [x] Normalize job (ffmpeg) for real media; Caddy signature check on `/media/*`; disk scan while there is no Plex token, storing uncarried shows for `/admin` (`phase-4/media`, stacked on `phase-10/custom-channels`)
- [ ] Normalize the real library: running on the rig since Nov 2 into the NAS `tv/.retro-cable` folder (`ops/rig/`, `m2/rig-worker`); the mini mounts it read-only and indexes its `manifest.json` (`media-index` service)
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

See "Assumptions to confirm" in `docs/plan.md`.

- Homelab host: **decided by Claude on 2026-11-01 (Brandon asked it to choose): the Mac mini** runs the stack and the NAS stores the media. Reasoning is in `docs/specs/ops.md` (Where it runs).
- The GHCR packages are private and the mini has no read token, so the mini builds images from its checkout (`RC_IMAGE_SOURCE=build`). To switch to pulling, create a classic PAT with only `read:packages` and run `docker login ghcr.io` on the mini.
