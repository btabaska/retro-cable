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

The scheduler's inputs are six YAML files in `catalog/`. Each starts with a `yaml-language-server` comment that points editors at its JSON Schema in `catalog/schema/`, generated from the pydantic models in `server/src/retro_cable/catalog/models.py` (`make catalog-schema`; a test fails if they are stale).

| File | Required | Holds |
| --- | --- | --- |
| `channels.yaml` | yes | Timezone, year range, channels: type (network, marathon, movie), launch, library, playlist, year-ranged identity (number, call sign, network), blocks, authenticity, transplant mark, bumper length |
| `holidays.yaml` | yes | Holidays: fixed or nth-weekday rule (n = -1 for the last), window, strength, default mode, per-channel modes, tagged items |
| `carriage.yaml` | no | Which show or movie airs on which channel from when until when, optionally in a block, with a weight and tags |
| `synthetic.yaml` | no | Transplants: a synthetic premiere inside 1990–2010, or `library: true` |
| `overrides.yaml` | no | Per show: Plex title, US debut, per-episode air dates, holiday tags, exclude |
| `templates.yaml` | no | Day-part templates per channel with era versions: parts per weekday at clock times, weighted pools of shows or carriage tags, block restriction, primetime flag |

- Show ids are slugs (`the-simpsons`). Carriage pairs each id with the Plex title used for matching; `overrides.yaml` can set a different `plex_title`.
- YAML dates and `1:30`-style values are read as text and parsed by the models, so a bad date names the file and field. Unquoted `on` is a YAML boolean and is rejected with the field name; holiday modes must be quoted.
- **Validation** (`make catalog-check`, also run by the server tests on the checked-in catalog) adds cross-file checks the schemas can't express:
  - unique ids, a valid timezone, and a year range within 1990–2010;
  - identity periods that don't overlap and that cover the launch date;
  - movie channels, and only movie channels, have a playlist;
  - fixed holiday dates exist, and holiday channels exist;
  - carriage names known channels and blocks, ends after it starts, and doesn't overlap for the same show and channel;
  - synthetic premieres fall within 1990–2010, or the entry is library;
  - every override and synthetic entry names a carried show, override holiday tags name known holidays, and no show has both a US debut and a synthetic premiere;
  - templates name known channels (never movie channels), their era periods don't overlap per channel, each weekday that appears has exactly one 00:00 part and no duplicate start times, block parts name known blocks, pool shows are carried on the channel, and pool tags appear in that channel's carriage.
- **Catalog version**: the first 12 hex characters of a SHA-256 over the six files. It is one of the four inputs that make a schedule a pure function.
- **US debut and synthetic premiere** use the same rule (`shift_air_dates`): the earliest episode moves to the anchor date and every other episode keeps its spacing. Missing dates stay missing.
- The first `carriage.yaml` (299 entries) was generated from `docs/archive-list.md`. Windows run from Jan 1 of the first year to Dec 31 of the last, clamped to the channel's launch; open-ended runs have no `until`. DCOMs air on Disney Channel from their premiere year and always on Disney Movies. `overrides.yaml` has best-effort US debut dates for the Toonami and Adult Swim anime. `synthetic.yaml` lists the archive list's transplant ideas as comments for Brandon to pick.
- The first `templates.yaml` (Claude-drafted, to edit) covers ktvu (Fox Kids era and after), cartoon-network (classics era and originals era) and adult-swim (late night, two eras). Everything else runs on the **default template**: one all-day part playing everything the channel carries that isn't block-carried, at carriage weights. A weekday with no part in the covering template also falls back to the default.

### Database

- SQLite, opened with foreign keys on and WAL. Migrations are numbered files in `server/src/retro_cable/db/migrations/`, applied in order, each in one transaction and recorded in `schema_migrations`.
- `0001_catalog` creates:
  - `shows`, keyed by catalog slug, with Plex, TMDB, TVDB and IMDb ids;
  - `episodes`, unique per show, season and number, holding the original `air_date` and its source (`plex` or `tmdb`), runtime, Plex key and file path (no file path means known but not on disk);
  - `movies`, with a premiere date;
  - `import_runs`.
- Overrides and synthetic dates are not written into the database. They are applied when the schedule is read, so editing the YAML never needs a re-import.

### Plex import (`python -m retro_cable.ingest plex`)

- Reads `PLEX_URL` and `PLEX_TOKEN` from the environment. The token is sent as the `X-Plex-Token` header, never in a URL, and is never printed.
- **What it reads:** walks the show and movie library sections:
  - shows from `/library/sections/{key}/all?type=2&includeGuids=1`;
  - episodes from `/library/metadata/{show}/allLeaves`;
  - movies from `/library/sections/{key}/all?includeGuids=1`.
- **Matching:** only carried titles are imported. Titles are compared case-, accent- and punctuation-insensitively, then again without a trailing `(year)`. `overrides.yaml` `plex_title` takes precedence, and `exclude` skips a show. Everything unmatched is listed in the run stats.
- **What it stores:** TMDB, TVDB and IMDb ids from Plex GUIDs, the original air date (`originallyAvailableAt`), runtime, and the first media part's file path and size. Items without an episode number (extras) are skipped.
- **Re-runs** are idempotent. A file Plex no longer has is detached (`file_path` cleared) but its episode row stays. Plex never erases a known air date or runtime with an empty value, and Plex air dates win over TMDB ones.
- Each run is recorded in `import_runs` with its stats.

### TMDB gap filling (`python -m retro_cable.ingest tmdb`)

- Reads `TMDB_TOKEN` (a v4 read access token, sent as a bearer header). This is the app's only runtime call outside the LAN, made by the worker.
- **Interface:** `MetadataSource` (`seasons`, `season`), with `TmdbClient` as the implementation. Tests use recorded responses and a fake.
- **For every show with a TMDB id:**
  - fills only empty air dates (source `tmdb`), runtimes and titles;
  - adds episodes Plex doesn't have, with no file, which gives the inventory its expected-versus-on-disk counts.
- A show that fails is listed and skipped. Shows without a TMDB id are listed.

### Inventory (`make inventory`)

- `python -m retro_cable.inventory --db --catalog --out` writes `inventory.md` and `inventory.json`. `make inventory` writes them to `reports/` (gitignored) from `data/retro-cable.db`, or from `RC_DB` if set.
- **Rows:** one per channel per year in the year range, only for years the channel is on the dial (launched, or library).
  - Shows: carried shows whose carriage window overlaps the year.
  - Expected: those shows' episodes eligible by Dec 31 of that year, plus every episode of a library show.
  - On disk: the expected episodes that have a file.
  - Missing: expected minus on disk.
  - Movies: carried movies already premiered by Dec 31 (or with an unknown premiere), and how many are on disk.
- **Effective air dates** (`effective_dates`, shared with the scheduler):
  - per-episode dates from `overrides.yaml` replace the originals (`S1E2` and `S01E02` are equivalent);
  - then a synthetic premiere, or failing that a US debut, shifts the whole show;
  - undated episodes get a conservative synthesized date (scheduler spec, "Undated episodes"); specials and shows with no debut bound stay undated and are never eligible.
- **Library rules:**
  - A library *channel* is always on the dial, but its items still respect their air dates (Halloweentown can't air before October 1998).
  - A library *show* (`synthetic.yaml`) has every episode eligible.
- **Gaps:**
  - carried shows not in Plex;
  - shows in Plex with no episode files;
  - episodes on disk without an air date;
  - carried shows without a TMDB id;
  - carried movies not on disk;
  - titles in Plex that aren't carried (from the last Plex import).
  - untagged specials: episodes of `seasonal` shows the tagger can't place (they never air) and movies of an hour or less (TV specials) with no holiday tag. Fix them with `holiday_tags` or a `tagged` entry.

## Implemented (Phase 7: holidays)

- `holidays.yaml` has Halloween, Thanksgiving, Christmas, New Year's Eve, Valentine's Day, St. Patrick's Day and Independence Day. Halloween lists the Halloween DCOMs the keywords miss (Under Wraps, Twitches) and Christmas lists Rudolph and Frosty as `special`s. The St. Patrick's Day config shows per-channel modes (`specials_only` by default, `on` for the Disney channels).
- `overrides.yaml` gains `seasonal: true` per show: a holiday specials show whose episodes air only in their holiday's season. The Peanuts holiday specials are marked.
- Tagging is at read time, not in the database, like overrides and synthetic dates: the tagger runs over the library's titles whenever the scheduler is built, so editing a keyword or override never needs a re-import. The plan's "classification pass at ingest" with a review list for low-confidence tags is not built; the keyword tagger plus overrides covers it until a local model joins in Phase 11. How tags and seasons drive the schedule is in `scheduler.md` (Phase 7).
- The demo seed titles every episode that airs in the week up to a holiday for it ("Episode 57: Halloween"), gives the Peanuts specials their real titles and dates, and gives the holiday DCOMs their real premieres (Halloweentown 1998-10-17).

## Implemented (Phase 10: custom channels)

Custom channels made in `/admin` are **catalog inputs**, not schedules, and never touch the YAML files.

- **Overlay.** `server/src/retro_cable/catalog/overlay.py`. An `Overlay` holds `channels[]` and `carriage[]` in the same models as `channels.yaml` and `carriage.yaml`. The effective catalog is the YAML catalog plus the head overlay (`apply_overlay`), validated by the same cross-file `validate()`. Overlay channel ids start with `custom-`, so they can't collide with YAML ids. In M1, YAML channels are read-only in the admin.
- **Version.** An empty overlay leaves the YAML version unchanged. Otherwise the version is `sha256(yaml version + canonical overlay JSON)[:12]`. A schedule stays a pure function of (channel, date, catalog version, seed), and undo brings back the exact previous version.
- **History.** Migration `0003_catalog_revisions` adds an append-only `catalog_revisions` table (`id`, `created_at`, `parent_id`, `overlay` as canonical JSON, `note`). The row with the highest id is the head.
  - The first save records an empty "start" row, then the new head.
  - Undo appends a copy of the head's parent, with the parent's own parent, so successive undos walk back and rows are never rewritten.
- **Draft to models** (`server/src/retro_cable/admin/drafts.py`):
  - The channel gets one identity from its launch (Jan 1 of the launch year), or from 1990-01-01 when it is library.
  - Each show's carriage runs from Jan 1 of its `from_year` (default: the launch) to Dec 31 of its `until_year` (default: open).
  - Movie channels get a `shuffled` playlist.
  - Custom channels have no bumpers yet (the bumpers table is per channel id).
- **Library.** Shows and movies come from the database, whether carried or not. The demo seed adds three uncarried shows (Recess, Gravity Falls, Over the Garden Wall). The Plex importer still stores only carried titles; storing uncarried ones is left for the ingest owner.
- **Not in M1:** synthetic premieres for transplants (a premiere is global to the show, so it would move the show on every channel), editing YAML channels, holiday modes per custom channel, per-row on/off toggles, and the nightly YAML export.

## Implemented (Phase 4: media)

### Database

- `0004_media` (after `0003_catalog_revisions`) adds `media_path`, `media_seconds`, `media_source_size` and `media_source_mtime_ns` to `episodes`, `movies` and `bumpers`. `media_path` is the normalized MP4, relative to the media root; `media_seconds` is its ffprobe duration, rounded down, and is the runtime the scheduler uses.

### Disk scan (`python -m retro_cable.ingest scan --tv DIR [--movies DIR]`)

A stopgap for while there is no Plex token: it reads Plex-named folders straight off the disk.

- **Matching:** each top-level folder is matched to a carried show with the Plex importer's rules (`plex_title` overrides, `exclude`, case- and punctuation-insensitive, without a trailing `(year)`), then also without spaces (`The OC` = `The O.C.`) and with a trailing article moved to the front (`Grim Adventures of Billy and Mandy, The -`). Unmatched folders are listed. Synology `@eaDir`, `#recycle` and dot-files are skipped.
- **Episode numbers:** `S01E02` (also `S1E2`, `S01E01-E02`, which records the first number), `1x02`, and inside a `Season N` folder `102 - Title` or `02. Title`. `Specials` is season 0. Files without a number are listed as unparsed; trailers, samples and extras are ignored.
- **Duplicates:** one file per episode, preferring a copy inside a `Season NN` folder, then the bigger file.
- **What it stores:** the carriage title, the premiere year (from `(Year)` in the folder, else the commonest `Show (Year) - Sxx` file name), and per episode the title from the file name, the absolute file path and size. It never writes air dates, and keeps titles, dates and runtimes that Plex or TMDB already set.
- **Re-runs:** files under the scanned root that are gone are detached (`file_path` cleared); rows outside that root are untouched.
- **Movies:** `<root>/<Title (Year)>/`, the biggest video that isn't a trailer or extra, with the year and no premiere date.
- Each run is recorded in `import_runs` (`scan`, `scan-movies`).
- On the NAS (2026-11-02) it matches 21 carried shows in `/volume3/tv` and finds 2,968 episodes. `Justice League (Unlimited)` needs a `plex_title` override to match `justice-league`.

### Normalize (`python -m retro_cable.ingest normalize`)

- **Output:** one MP4 per episode, movie and bumper with a source file, at `tv/<show>/sNN/<show>-sNNeNN.mp4`, `movies/<id>.mp4` or `bumpers/<channel>/<name>.mp4` under `--media-dir` (`RC_MEDIA_DIR`).
- **Encoding:** H.264 High, yuv420p, CRF 23 capped at 1.5 Mbit/s; AAC stereo 128 kbit/s at 48 kHz from the English audio track if tagged, else the first; no subtitles, data or metadata. A keyframe every second (`-force_key_frames`), and the index at the front (`+faststart`).
- **Picture:** deinterlaced when frames are flagged interlaced (`bwdif`), at most 480 lines, never upscaled, square pixels with the display aspect kept (anamorphic sources are corrected). A picture within 3% of 4:3 or 16:9 is drawn at exactly that shape. With `--crop auto` (default), black bars baked into the picture are cropped first: cropdetect samples 4 s at 20%, 50% and 80% of the running time, the crop is the union of the three boxes, and bars under 2.5% a side are left alone. This turns 4:3 shows released in 16:9 frames (The Simpsons and Courage web releases) back into 640×480.
- **Encoders:** `--encoder x264` (libx264 `veryfast`, default) or `--encoder nvenc` (`h264_nvenc`, preset p5, VBR CQ 23, forced IDR keyframes); also `RC_ENCODER`.
- **Sources:** absolute paths (as Plex or `scan` saw them) go through `--source-map FROM=TO` (or `RC_SOURCE_MAP`, comma-separated); relative ones resolve under `--source-root` (`RC_SOURCE_ROOT`). A missing source is listed and skipped.
- **Idempotent and resumable:** an item is skipped when its output exists and the source size and mtime match what was recorded; `--force` re-encodes. Each encode goes to `<name>.partial`, is read back with ffprobe, renamed, and committed on its own, so an interrupted run leaves no half files and the next run carries on. A failed item is listed and the run continues.
- **Trials:** `--limit N` encodes at most N items, `--max-seconds S` keeps the first S seconds, `--dry-run` prints the ffmpeg commands.
