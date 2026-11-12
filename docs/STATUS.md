# Status

**Current phase:** Phase 9 (the Apple TV app), started Nov 11 2026: the app is built and in review as `m9/tvos`, waiting for Brandon to sideload it. Alongside it, Phase 8 (operations), started Nov 11 2026 while the rig converts the Live TV backlog: nightly backups with a restore test and the Sunday report are in review as `m6/operations`, stacked on M5. Before that, Milestone M2: real media on the mini; M3 (the first slice of Phase 11's channel builder) is in review on top of it, M4 (Live TV downloads) on top of M3, and M5 (the weekly station manager) on top of M4. M1 was approved by Brandon on Nov 2 2026. Phases 3–7 and 10 (part) are still in review as a stacked set of PRs; M2 is #16 (`m2/integration` = #14 + #15 + #3).

## Phase 9: the Apple TV app (in review, `m9/tvos`, stacked on `m7/descriptions` at `c8e0e3d`)

Brandon (Nov 11 2026): an Apple TV app with the browser's TV screens, sideloaded from Xcode with a free Apple ID (7 days a build), on his Apple TV 4K (2nd gen, tvOS 18+). Specs: `tvos.md` (TV-1 ... TV-44), `docs/design/tvos.md` (D-1 ... D-14). Build log: `tvos/HANDOFF.md`; Brandon's sideload guide: `tvos/README.md`. No server change: the app uses the API as it is.

- [x] `RetroCableKit` (Swift package, tvOS 18 + macOS 14, Swift 6 strict concurrency): API client and models checked against `server/openapi.json`, design tokens as Swift constants with a staleness test, ports of the web's pure logic and its tests, the tuner, banner, guide grid, year picker and watch reporter; 236 tests on macOS (`make test-tvos`)
- [x] App (SwiftUI + AVKit, XcodeGen `tvos/project.yml`): power on, no signal, Live TV with one `AVPlayer` (aspect-fit on black, no system transport UI), the info banner and clock, Siri Remote mapping, the guide with details and the live PiP, the year picker with the two-column dial; Arimo and Archivo Narrow bundled (`make tvos`)
- [x] XCUITests for the browser's flow (launch → banner → guide → tune → change year), the remote, no signal and the background round trip, run in the "RC Apple TV" simulator against the homelab (`make test-tvos-ui`); screenshots of every screen in `tvos/build/screens/` (gitignored)
- [x] Five new color tokens (glow, track, divider, digits, filler) in `design/tokens.json`; `make lint` and `make test` unchanged and green
- [ ] Brandon: sideload onto the Apple TV (`tvos/README.md`) and the hands-on checks: TV-2, TV-5, TV-6, TV-7, TV-8 (an evening), TV-27, TV-34, TV-39, TV-40 (p95 tune time over 20 flips), TV-43 (same program as the browser at the same moment)
- [x] Review round 1 (Nov 12): the RC app icon, the Personal Team kept in a gitignored `tvos/Local.xcconfig`, "Tuning…" bars during a /tune outage, a true 5 s retry cadence, the TV-on white line, no lone "…" in narrow guide cells; the UI tests pass against the Docker test stack (`RC_SERVER_URL`)
- [ ] A long run across simulated midnight, and a pixel overlay of the tvOS screenshots on the web references (D-14; eyeballed so far)
- Not in this build, by decision: likes (hold Select), a year-picker shortcut on Live TV, the app icon, TestFlight and fastlane (they need the paid account)

## Phase 8 (part): backups and the Sunday report (in review, `m6/operations`, stacked on #24)

Brandon (Nov 11 2026): keep building while the downloads and conversion run. Specs: `ops.md` (Nightly backups and the restore test; The Sunday report), `api.md` and `web.md` (the Report tab).

- [x] Nightly backups: `python -m retro_cable.backup take` (SQLite backup API, gzipped, retention 14 nights / 8 weeks / 12 months) by `ops/backup.sh` at 1 AM, kept in the data dir and on the NAS `backups` share (`/mnt/nas-backups/retro-cable` on the mini), before the mini's restic run takes it offsite
- [x] The restore test every night on the NAS copy: integrity, migrations, the /admin overlay through the catalog checks, and a day on every channel in 1995, 2000 and 2005; schedule findings are warnings for the report
- [x] The Sunday report: viewing, the station manager's run and what waits for approval, what is on the air and what carried titles still lack, the conversion, backups; saved for /admin's new Report tab and pushed through the homelab's ntfy when `RC_NTFY_URL` is set; Sundays 8 AM
- [x] Timer jobs alert through the homelab's `ntfy-notify@` when they fail
- [x] On the mini (Nov 11): the NAS backups share mounted at `/mnt/nas-backups`, `RC_BACKUP_DIR` set, deployed, `sudo ops/systemd/install.sh`; the first backup (1.4 MB) and restore test ran
- [ ] Brandon: an ntfy topic and token for the report (`RC_NTFY_URL`, `RC_NTFY_TOKEN`, `RC_REPORT_LINK` in the mini's `ops/.env`); until then it is in /admin only
- [ ] Brandon (Home repo): add `/opt/retro-cable-data/backups` to the mini's restic `BACKUP_PATHS`, as the Home runbook planned "once it holds state"
- Not yet in Phase 8: telemetry and dashboards (Prometheus, Loki, Grafana), patching, the cert and DNS check

## Milestone M5: the weekly AI station manager (in review, stacked on #23)

Brandon (Nov 7 2026): "make sure it runs on a weekly schedule, like Sundays at 2am, to build the schedule using viewership data from what has been watched and when." Specs: `catalog.md` and `ops.md` (M5), `api.md` and `web.md` (M5: viewing reports), `scheduler.md` (M5: power hours).

- [x] Viewing data: the TV reports each program it plays (heartbeat every minute, program end, tune-away, page hidden, power-off by `sendBeacon`; never blocks tuning, failures silent; under 10 s is surfing and isn't sent) to `POST /api/watch`, stored in `watch_sessions` (migration 0007) with no IPs, tokens or URLs
- [x] Signals in code: watch time, completion and early tune-aways (under 3 minutes) per channel and show, channel dwell, an hour-of-week heatmap in New York time
- [x] `python -m retro_cable.station_manager run [--dry-run] [--weeks 4]`: under 2 hours of viewing in the window it records "not enough viewing yet" and changes nothing; otherwise the rig's local model proposes weight changes and a power hour (Brandon's busiest hour, every day, from his most finished shows per channel)
- [x] Guardrails in code: weights and power hours only (nothing carried or dropped, no dates, launches, premieres or holidays), the authenticity dial caps drift (default 30: x0.62 to x1.62), at most a fifth of a channel's weights per week, the /admin checks and a week's smoke test per channel; a Hypothesis property keeps the scheduler invariants
- [x] Auto-apply on by default (`RC_STATION_AUTO_APPLY=1`): one /admin revision per week, undone in one tap; every run (applied, proposed, dry, skipped, failed) recorded and shown in /admin's new Station tab (`GET /api/admin/station/runs`)
- [x] Timer: `ops/systemd/retro-cable-station-manager.{service,timer}`, Sundays 02:00 America/New_York, `Persistent=true`, running the job in the api container; shares a lock with `deploy.sh`
- [x] On the mini (Nov 7 2026): `RC_LLM_URL`, `RC_LLM_MODEL` and `RC_LLM_KEY` in `/opt/retro-cable/ops/.env`, deployed, `sudo ops/systemd/install.sh` run
- [ ] Brandon watches for a couple of weeks (2 hours is the minimum), then reads the first applied run in /admin → Station
- Not yet: likes (hold Select on the banner), the authenticity dial in /admin, Telegram reports

## Milestone M4: Live TV downloads (in review, stacked on #21)

Shows Retro Cable needs that aren't on the NAS are downloaded, never into Plex (Brandon, Nov 7 2026). The homelab side is live (Home task `retro-cable-02`): `sonarr-livetv` (:8990) and `radarr-livetv` (:7879) on the NAS save into the LiveTV share's `sources/tv` and `sources/movies`, profile `LiveTV SD`, unmonitoring deleted files. See `docs/specs/ops.md`, "Live TV downloads (M4)".

- [x] `ingest scan` reads several roots as one library (`--tv`/`--movies` repeat); the rig worker adds `/media/sources/tv` and `/media/sources/movies`. Plex's copy wins; a missing root detaches nothing; the empty-Plex-tree guard stays for Plex's tree only
- [x] Originals under `RC_DELETE_SOURCES_UNDER` (`/media/sources` on the rig; unset: never) are deleted once their encode is whole and checked; realpath check, no symlinks, never Plex's trees. The episode keeps its encode and manifest entry (`source_deleted_at`) and is not converted again
- [x] `ingest acquire [--dry-run]`: carried titles with no files anywhere go to sonarr-livetv / radarr-livetv (by library id, else exact title + first-aired year; unresolved otherwise). Refuses an instance whose root folders aren't exactly `/tv` / `/movies`; idempotent. Runs in the worker only with `RC_ACQUIRE=1` (off)
- [x] Brandon's final list (Nov 7, 410 titles: 204 shows, 173 movies, 33 specials) applied to the YAML (`m4/final-lineup`, stacked on #22). `python -m retro_cable.channel_builder.lineup prune` kept 98 carried titles and removed 177 (61 `remove` rows, 116 not in the list). Templates were fixed where their pools emptied; no template was dropped and every channel still carries something. The other 313 kept titles are for the channel builder to place. Kept titles have the list's TMDB id and first-aired year in `overrides.yaml`; the builder records them for the shows it places (overlay `ids[]`); acquire prefers them; `/api/admin/library` lists carried titles with nothing on disk, so the rig sees builder-placed shows
- [x] `acquire --dry-run` on the real instances (read-only, Nov 7): 80 carried titles have no files; 72 resolve (65 by TMDB id) and 8 don't (5 maybe already on the NAS under another folder name, 3 with no TMDB id or year). Details in `docs/specs/ops.md`
- [x] First live acquire on Nov 8 (rig worker on `m5/station-manager`): 137 shows and 159 movies added. The unresolved titles were fixed by lookup-only overrides, the qualifier rule, strict TMDB matches, `part_of` (shows TheTVDB files as seasons of another series: Animaniacs 2020, Justice League Unlimited, Pokémon Horizons, The New Scooby and Scrappy-Doo Show, the Lupin parts; the scheduler airs them as their own shows) and `episodes_of` (TV movies that are episodes: Sozin's Comet, Really Big Shrimp, Reggie's Big Beach Break, joined on the rig by `ingest assemble` once their episodes convert; Heidi (1993) the same way, from TheTVDB's two-part miniseries). Mini deployed at `sha-413eeb9`
- [x] Acquire on: `RC_ACQUIRE=1` and the two API keys on the rig since Nov 8 2026; the rig worker downloads what carried titles lack, every pass
- [ ] Brandon reviews the prune (judgment calls in the PR; KTVU's Fox Kids block now has nothing in 1990–91 and 1996–98) and the dry run, then sets `RC_ACQUIRE=1` and the two API keys in the rig's `ops/rig/.env` and redeploys the rig worker

## Milestone M3 / Phase 11 (part): AI channel builder (in review, stacked on #18)

- [x] `python -m retro_cable.channel_builder propose --shows CSV --out DIR` drafts channels from Brandon's show list on the rig's local model (LiteLLM, `RC_LLM_URL`/`RC_LLM_KEY`/`RC_LLM_MODEL`). The model names channels and places shows. The code does the dates, pretend premieres for post-2010 shows, launch years, numbers, call signs, ids and the checks. Nothing is saved
- [x] Every proposal goes through the /admin checks. Failing channels are reported and left out. Every kept show is placed, already carried, or unplaced with a reason
- [x] Readable output for review: `proposal.md` (channel → shows) and `proposal.csv`, beside `proposal.json`
- [x] `apply proposal.json` is a separate command: one admin overlay revision, undone in one tap. The overlay now also holds the builder's day-part templates
- [x] Hypothesis: any proposal that passes the checks keeps the scheduler invariants once applied
- [ ] Brandon's review of a full-list proposal, then `apply` on the mini (it decides what the rig converts)

## Milestone M2: real library, debut rule everywhere, LAN deploy (in review, #16)

- [x] Debut rule on every channel, including library and custom ones; `library: true` only keeps a channel on the dial before its launch (#15)
- [x] Pretend premieres per (show, channel) in `/admin`: set, edit and clear them; the transplant mark and NEW follow the shifted dates (#15)
- [x] M1 rough edges: Cartoon Network evenings, Disney Movies rotation, focus return, errors, borders, chevrons, 360 px chips, narrow cells, holiday colour (#15)
- [x] Rig worker (`ops/rig/`): GPU-aware encoder (NVENC only when the GPU is idle and ComfyUI's queue is empty, otherwise x264 at low priority), priority order, resumable, manifest (#14)
- [x] Mini: read-only automount of the NAS `LiveTV` share (volume1) at `/mnt/nas-livetv` (moved from `tv/.retro-cable` on Nov 2); the `media-index` service records new encodes from `manifest.json` every 5 minutes, with no redeploy (#14)
- [x] Deployed to the mini with `ops/deploy.sh` (build mode, `m2/integration`), LAN only at https://tv.home.tabaska.us through the homelab Caddy; the web port stays on loopback. Checked on Nov 2 from the Mac with Playwright (WebKit and Chromium): power on in 1995, KTVU plays a converted episode at the tune offset, the banner hides, and `/admin` loads; the next program on KTVU, Cartoon Network and Boomerang serves a 206 byte range
- [ ] Conversion of the real library: running on the rig since Nov 2. Only shows a channel carries (YAML, or a channel made in `/admin`), 13 episodes per show per turn, earliest year first; uncarried shows are not converted (Brandon, Nov 2). About 50–80 s per episode, so roughly 2 days for the 2,980 carried episodes (about Nov 4–5). 110 were done at 21:10 UTC on Nov 2; the `/admin` Media tab shows live progress and an ETA
- Real episodes on the dial right now (Nov 2, 21:10 UTC): KTVU (The Simpsons 1990–2010; Batman: The Animated Series 1993–95; Animaniacs 1994–95), Cartoon Network and Boomerang (Scooby-Doo 1993–2010; on Cartoon Network also Johnny Bravo, Ed, Edd n Eddy, Courage from 1998–2000), Comedy Central (South Park 1998–2010), Toonami (Batman, 2000). Best years to try: 1995 and 1999–2001. Other channels show Off Air until their shows convert
- [x] `/admin` Media tab backed by `GET /api/admin/media/progress`, read from the `progress.json` the rig worker keeps on the NAS (#14). Encodes count only when whole (video, audio, length within 2% of the source); `/health` and `media-index` now fail a deploy on an unwritable data dir; the web container rejects non-private clients itself (#14)
- [ ] Plex import and TMDB fill: `PLEX_URL` and `PLEX_TOKEN` are in the mini's `ops/.env` (Nov 2, read from the NAS Plex server's own settings; the token authenticates). `TMDB_TOKEN` is set too, and the mini's `tmdb` service fills air dates the library lacks (`COMPOSE_PROFILES=media-index,tmdb`)
- [ ] Brandon's check: open the TV from a LAN device; real episodes play on their channels, and at least three channels tune and play past a program boundary

Brandon's show lineup reached `catalog/carriage.yaml` in M4 (`m4/final-lineup`). Brandon approved M2 on Nov 2, with its defaults (library channels keep their shows; weekly cadence after a pretend premiere; label and colours; NVENC bitrate); only carried shows convert, and the rig reads the mini's `/admin` library.

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
- [ ] Normalize the real library: running on the rig since Nov 2, in HEVC from Nov 2 evening, into the NAS `LiveTV` share (`ops/rig/`, `m2/rig-worker`); the mini mounts it read-only and indexes its `manifest.json` (`media-index` service)
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
