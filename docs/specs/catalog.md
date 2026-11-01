# Catalog spec

Split from `docs/plan.md` (approved Nov 1, 2026) without changing its meaning; plan text is copied verbatim. Where this spec and the plan disagree, this spec wins for its area. Related specs: `scheduler.md`, `api.md`, `web.md`, `ops.md`.

## Decisions locked

| Area | Decision |
| --- | --- |
| Holidays | Holiday episodes and specials favored near the holiday; holiday calendar is editable |
| Movies | Yes: Disney Channel Original Movies, holiday specials, movie channels |
| Toonami, Adult Swim | Their own 24/7 channels |
| Region | Mountain View, CA (San Francisco Bay Area stations) |
| Archive list | Claude proposes a full lineup per channel; Brandon prunes |

## Custom channels and the AI station manager

All of these change the scheduler's inputs (channels, carriage, dates, holidays, weights), never the schedule itself, so one set of tests guards your edits and the AI's alike.

### Your own channels

- A channel is a YAML file or an admin form: name, number, logo, bumper set, launch year (or "always on") and its templates.
- Three channel types: **network** (day-part templates), **marathon** (one or a few shows in order), **movie** (a hand-curated playlist in your order or shuffled).
- A "Netflix" channel is a network or marathon channel built from your own copies of those shows, with synthetic dates.

### Synthetic air dates

- Any show can get a synthetic premiere date inside 1990–2010. The spacing between its real episode dates is kept, so a season still unrolls week by week.
- Or mark a show "library": every episode available whenever its channel is on, like Boomerang.
- Transplants slot into real blocks: Gravity Falls (2012) with Disney Channel carriage from a synthetic 2004 premiere airs beside Kim Possible.
- The guide can show a small mark on transplanted shows or hide it, per channel.

### Shows still running today

- Only episodes aired by the simulated date are eligible, and the latest simulated date is Dec 31, 2010. SpongeBob, Sesame Street, Arthur and The Simpsons only need seasons through 2010 archived.
- Pokémon isn't in the lineup yet: it aired on Kids' WB before moving to Cartoon Network in the mid-2000s. Add it as Cartoon Network carriage, a transplant, or a Kids' WB block.

### Holidays you control

- Holidays live in `catalog/holidays.yaml`: name, date rule (fixed like Mar 17, or rules like the fourth Thursday of November), window length, strength and tags.
- St. Patrick's Day is included with The Luck of the Irish (Disney Channel, 2001) tagged; the tagger proposes more candidates for you to confirm.
- Per channel, each holiday is on, off or specials only.


### AI channel builder (local model)

- Give it a channel name, the shows you want and a vibe ("Saturday morning, 1996"). Your local model drafts templates, carriage, synthetic dates, holiday tags and bumper style.
- The draft is YAML validated against a schema; you approve it after a week's preview, in the admin or in Telegram.
- It runs on the 3090 Ti through LiteLLM, so nothing at runtime depends on Claude.

### AI station manager (weekly, local model)

- Signals: watch time and completion per show, early tune-aways (under 3 minutes), channel dwell, an hour-of-week heatmap of when you watch, and likes (hold Select on the info banner).
- Each Sunday it proposes a diff: more weight for shows you finish, less for ones you skip, rebalanced day-parts.
- **Power hour**: your busiest viewing hours (say 8–9 p.m.) become a day-part where every channel pulls from your top shows on that channel.
- An authenticity dial per channel (0 = era-accurate, 100 = all favorites) caps the drift; proposed default 30.
- Guardrails: at most about a fifth of slots change per week, date eligibility never bends, the same property tests must pass, every change is logged with one-tap rollback.
- Hermes runs it and sends you the proposal; auto-apply is a toggle.

### Admin panel

The admin panel is specified in `web.md` (Admin panel).

## Media pipeline and storage

Plan on 4–6 TB for the full proposed lineup at 480p, or 2–3 TB once you prune it by half.

### Pipeline

1. **Archive** your copies to the NAS in Plex naming (`Show (Year)/Season 01/Show - S01E01.ext`). Bumpers and promos go in `_bumpers/<channel>/<era>/`.
2. **Match** in Plex as you do today. Plex's TVDB/TMDB matching supplies each episode's original air date.
3. **Import**: a catalog job reads the Plex API (show and episode GUIDs, air dates, runtimes, file paths) into the app's SQLite catalog. TMDB fills gaps; a YAML file holds US-debut and carriage overrides.
4. **Normalize**: an ffmpeg job writes H.264 + AAC stereo MP4s at 480p (4:3 kept as 4:3) with the index at the front, so playback can start mid-file. It uses NVENC on the 3090 Ti box when free, the NAS CPU otherwise.
5. **Tag**: a classification pass marks holiday episodes from titles and summaries; low-confidence tags go to a review list.
6. **Report**: an inventory by channel and year (episodes on disk vs. expected, missing shows, untagged specials). These are your measurements.

The app plays the normalized files straight from Caddy, so Plex is a metadata source, not in the playback path.

### Storage math

At about 1.2 Mbps video plus 128 kbps audio, files run about 10 MB per minute (approximate).

| Item | Count (approx., full list) | Size each | Total |
| --- | --- | --- | --- |
| Half-hour episodes (~22 min) | 15,000 | 220 MB | 3.3 TB |
| Hour episodes (~44 min) | 2,500 | 440 MB | 1.1 TB |
| Movies and specials | 150 | 0.9 GB | 0.14 TB |
| Bumpers and promos | 2,000 | 5 MB | 0.01 TB |
| **Total** | | | **~4.6 TB** |

The inventory job replaces these estimates with real counts after the first import.

## Testing

| Layer | Tests | Runs |
| --- | --- | --- |
| Ingest | Plex API from recorded responses; ffmpeg on a sample clip; tagging with a stubbed model | Every PR |

## Archive list

The per-channel show list to prune is in `docs/archive-list.md`.

## Implemented (Phase 2)

### Catalog files

The scheduler's inputs are five YAML files in `catalog/`. Each starts with a `yaml-language-server` comment that points editors at its JSON Schema in `catalog/schema/`, generated from the pydantic models in `server/src/retro_cable/catalog/models.py` (`make catalog-schema`; a test fails if they are stale).

| File | Required | Holds |
| --- | --- | --- |
| `channels.yaml` | yes | Timezone, year range, channels: type (network, marathon, movie), launch, library, playlist, year-ranged identity (number, call sign, network), blocks, authenticity, transplant mark |
| `holidays.yaml` | yes | Holidays: fixed or nth-weekday rule (n = -1 for the last), window, strength, default mode, per-channel modes, tagged items |
| `carriage.yaml` | no | Which show or movie airs on which channel from when until when, optionally in a block, with a weight and tags |
| `synthetic.yaml` | no | Transplants: a synthetic premiere inside 1990–2010, or `library: true` |
| `overrides.yaml` | no | Per show: Plex title, US debut, per-episode air dates, holiday tags, exclude |

- Show ids are slugs (`the-simpsons`). Carriage pairs each id with the Plex title used for matching; `overrides.yaml` can set a different `plex_title`.
- YAML dates and `1:30`-style values are read as text and parsed by the models, so a bad date names the file and field. Unquoted `on` is a YAML boolean and is rejected with the field name; holiday modes must be quoted.
- **Validation** (`make catalog-check`, also run by the server tests on the checked-in catalog) adds cross-file checks the schemas can't express:
  - unique ids, a valid timezone, and a year range within 1990–2010;
  - identity periods that don't overlap and that cover the launch date;
  - movie channels, and only movie channels, have a playlist;
  - fixed holiday dates exist, and holiday channels exist;
  - carriage names known channels and blocks, ends after it starts, and doesn't overlap for the same show and channel;
  - synthetic premieres fall within 1990–2010, or the entry is library;
  - every override and synthetic entry names a carried show, override holiday tags name known holidays, and no show has both a US debut and a synthetic premiere.
- **Catalog version**: the first 12 hex characters of a SHA-256 over the five files. It is one of the four inputs that make a schedule a pure function.
- **US debut and synthetic premiere** use the same rule (`shift_air_dates`): the earliest episode moves to the anchor date and every other episode keeps its spacing. Missing dates stay missing.
- The first `carriage.yaml` (299 entries) was generated from `docs/archive-list.md`. Windows run from Jan 1 of the first year to Dec 31 of the last, clamped to the channel's launch; open-ended runs have no `until`. DCOMs air on Disney Channel from their premiere year and always on Disney Movies. `overrides.yaml` has best-effort US debut dates for the Toonami and Adult Swim anime. `synthetic.yaml` lists the archive list's transplant ideas as comments for Brandon to pick.

### Database

- SQLite, opened with foreign keys on and WAL. Migrations are numbered files in `server/src/retro_cable/db/migrations/`, applied in order, each in one transaction and recorded in `schema_migrations`.
- `0001_catalog` creates:
  - `shows`, keyed by catalog slug, with Plex, TMDB, TVDB and IMDb ids;
  - `episodes`, unique per show, season and number, holding the original `air_date` and its source (`plex` or `tmdb`), runtime, Plex key and file path (no file path means known but not on disk);
  - `movies`, with a premiere date;
  - `import_runs`.
- Overrides and synthetic dates are not written into the database. They are applied when the schedule is read, so editing the YAML never needs a re-import.
