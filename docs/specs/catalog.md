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
- **Changed in M3 (Brandon, Nov 2 2026):** the input is a list of *shows*, not a channel name and a vibe. Brandon picks shows only; the builder decides which channels they go on, and channels may be made up. The draft is JSON (with a readable markdown and CSV), approved by running `apply`, not yet in /admin or Telegram. See "Implemented (M3: channel builder)" below.

### AI station manager (weekly, local model)

- Signals: watch time and completion per show, early tune-aways (under 3 minutes), channel dwell, an hour-of-week heatmap of when you watch, and likes (hold Select on the info banner).
- Each Sunday it proposes a diff: more weight for shows you finish, less for ones you skip, rebalanced day-parts.
- **Power hour**: your busiest viewing hours (say 8–9 p.m.) become a day-part where every channel pulls from your top shows on that channel.
- An authenticity dial per channel (0 = era-accurate, 100 = all favorites) caps the drift; proposed default 30.
- Guardrails: at most about a fifth of slots change per week, date eligibility never bends, the same property tests must pass, every change is logged with one-tap rollback.
- Hermes runs it and sends you the proposal; auto-apply is a toggle.
- **Changed in M5 (Brandon, Nov 7 2026):** it runs on a systemd timer on the mini every Sunday at 2 a.m. (not Hermes), auto-apply is on by default, and the report is in /admin's Station tab, not a chat message. Likes are not built yet. See "Implemented (M5: the weekly station manager)" below.

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
| `synthetic.yaml` | no | Transplants: a synthetic ("pretend") premiere inside 1990–2010 for one show on one channel |
| `overrides.yaml` | no | Per show: Plex title, US debut, per-episode air dates, holiday tags, exclude, seasonal, and (M4) the lineup's TMDB id (`tv:N` / `movie:N`), first-aired year, `part_of` (the seasons of another series it is; see `scheduler.md`) and `episodes_of` (a TV movie TheTVDB files as episodes of a series: `{ show, tvdb_id?, episodes: [S04E17, …] }`, `tvdb_id` when no channel carries the series, joined on the rig by `ingest assemble`, see `ops.md`) |
| `templates.yaml` | no | Day-part templates per channel with era versions: parts per weekday at clock times, weighted pools of shows or carriage tags, block restriction, primetime flag |

- Show ids are slugs (`the-simpsons`). Carriage pairs each id with the Plex title used for matching; `overrides.yaml` can set a different `plex_title`.
- YAML dates and `1:30`-style values are read as text and parsed by the models, so a bad date names the file and field. Unquoted `on` is a YAML boolean and is rejected with the field name; holiday modes must be quoted.
- **Validation** (`make catalog-check`, also run by the server tests on the checked-in catalog) adds cross-file checks the schemas can't express:
  - unique ids, a valid timezone, and a year range within 1990–2010;
  - identity periods that don't overlap and that cover the launch date;
  - movie channels, and only movie channels, have a playlist;
  - fixed holiday dates exist, and holiday channels exist;
  - carriage names known channels and blocks, ends after it starts, and doesn't overlap for the same show and channel;
  - each synthetic entry names a channel and a premiere within 1990–2010, the show is carried on that channel, and no (show, channel) pair is listed twice (M2; `library: true` entries are gone);
  - every override names a carried show, unless it only helps find a title (`tmdb_id`, `year`, `plex_title`, `part_of`, `episodes_of`: the builder's shows are carried in /admin), and override holiday tags name known holidays. `part_of` never names the show itself and a series season airs as one show only; `episodes_of` never names the movie itself, lists each episode once (`S4E7` is `S04E07`), and isn't combined with `part_of` (M4). A show may have a US debut and a synthetic premiere: the premiere wins on its own channel only (M2; before, both were an error);
  - templates name known channels (never movie channels), their era periods don't overlap per channel, each weekday that appears has exactly one 00:00 part and no duplicate start times, block parts name known blocks, pool shows are carried on the channel, and pool tags appear in that channel's carriage.
- **Catalog version**: the first 12 hex characters of a SHA-256 over the six files. It is one of the four inputs that make a schedule a pure function.
- **US debut and synthetic premiere** use the same rule (`shift_air_dates`): the earliest episode moves to the anchor date and every other episode keeps its spacing. A US debut applies on every channel; a synthetic premiere only on its channel, where undated episodes then follow weekly (`premiere_dates`, scheduler spec, M2). Elsewhere missing dates get the conservative synthesized date.
- The first `carriage.yaml` (299 entries) was generated from `docs/archive-list.md`; in M4 it was pruned to Brandon's final list (118 entries, below). Windows run from Jan 1 of the first year to Dec 31 of the last, clamped to the channel's launch; open-ended runs have no `until`. DCOMs air on Disney Channel from their premiere year and always on Disney Movies. `overrides.yaml` has best-effort US debut dates for the Toonami and Adult Swim anime. `synthetic.yaml` lists the archive list's transplant ideas as comments for Brandon to pick.
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
- **Which id (Nov 9 2026):** the lineup's (`tmdb_id: tv:N` in the effective overrides, the admin overlay's ids included), else the one Plex gave the show. Shows that are seasons or episodes of another series (`part_of`, `episodes_of`) take no lineup id: their episodes live under that series. Before, only Plex's ids counted, so the shows known from the rig's encodes alone (the Live TV downloads) were never filled: 1,020 encoded episodes on the mini had no air date.
- **For every show with a TMDB id:**
  - fills only empty air dates (source `tmdb`), runtimes and titles;
  - but only where the titles agree: an episode whose title differs from TMDB's under the same number (TMDB numbers some shows differently from TheTVDB, which the files follow: Dora the Explorer, Dragon Tales) keeps its empty date, and is listed under `mismatched_titles`. Titles agree when they are alike (punctuation and case aside, a 0.6 similarity), or one starts with the other (a file holding two segments, `Lucky Stone and The Mefirst Wizard`, is TMDB's `Lucky Stone`); an empty title can't tell and is filled;
  - adds episodes Plex doesn't have, with no file, which gives the inventory its expected-versus-on-disk counts. Only under an id from Plex, whose numbering is TMDB's: a later file under a TMDB-only number would take that episode's date.
- A show that fails is listed and skipped. Shows without a TMDB id are listed.
- `--watch SECONDS` fills again every that many seconds (the mini's `tmdb` service, docs/specs/ops.md), reading the lineup again each time.

### Inventory (`make inventory`)

- `python -m retro_cable.inventory --db --catalog --out` writes `inventory.md` and `inventory.json`. `make inventory` writes them to `reports/` (gitignored) from `data/retro-cable.db`, or from `RC_DB` if set.
- **Rows:** one per channel per year in the year range, only for years the channel is on the dial (launched, or library).
  - Shows: carried shows whose carriage window overlaps the year.
  - Expected: those shows' episodes eligible by Dec 31 of that year on that channel.
  - On disk: the expected episodes that have a file.
  - Missing: expected minus on disk.
  - Movies: carried movies already premiered by Dec 31 (or with an unknown premiere), and how many are on disk.
- **Effective air dates** (`effective_dates`, shared with the scheduler):
  - per-episode dates from `overrides.yaml` replace the originals (`S1E2` and `S01E02` are equivalent);
  - then, on a channel with a synthetic premiere for the show, the premiere shifts the whole show there (other channels keep the real dates); otherwise a US debut does;
  - undated episodes get a conservative synthesized date (scheduler spec, "Undated episodes"); specials and shows with no debut bound stay undated and are never eligible.
- **Library rules:**
  - A library *channel* is always on the dial, but its items still respect their air dates (Halloweentown can't air before October 1998). Inventory rows for a channel count its shows with that channel's synthetic premieres.
  - There are no library *shows* any more (M2): to air a show before its debut, give it a synthetic premiere on the channel.
- **Gaps:**
  - carried shows not in Plex;
  - shows in Plex with no episode files;
  - episodes on disk without an air date;
  - carried shows without a TMDB id;
  - carried movies not on disk;
  - titles in Plex that aren't carried (from the last Plex import).
  - untagged specials: episodes of `seasonal` shows the tagger can't place (they never air) and movies of an hour or less (TV specials) with no holiday tag. Fix them with `holiday_tags` or a `tagged` entry.
  - short media: episodes and movies whose file is shorter than half the expected runtime (samples, truncated encodes), as `show S01E02 (120 s of 1320 s)`. They never air unless `RC_ALLOW_SHORT_MEDIA=1`; the command also warns on stderr when there are any.

## Implemented (Phase 7: holidays)

- `holidays.yaml` has Halloween, Thanksgiving, Christmas, New Year's Eve, Valentine's Day, St. Patrick's Day and Independence Day. Halloween lists the Halloween DCOMs the keywords miss (Under Wraps, Twitches) and Christmas lists Rudolph and Frosty as `special`s. The St. Patrick's Day config shows per-channel modes (`specials_only` by default, `on` for the Disney channels).
- `overrides.yaml` gains `seasonal: true` per show: a holiday specials show whose episodes air only in their holiday's season. The Peanuts holiday specials are marked.
- Tagging is at read time, not in the database, like overrides and synthetic dates: the tagger runs over the library's titles whenever the scheduler is built, so editing a keyword or override never needs a re-import. The plan's "classification pass at ingest" with a review list for low-confidence tags is not built; the keyword tagger plus overrides covers it until a local model joins in Phase 11. How tags and seasons drive the schedule is in `scheduler.md` (Phase 7).
- The demo seed titles every episode that airs in the week up to a holiday for it ("Episode 57: Halloween"), gives the Peanuts specials their real titles and dates, and gives the holiday DCOMs their real premieres (Halloweentown 1998-10-17).

## Implemented (Phase 10: custom channels)

Custom channels made in `/admin` are **catalog inputs**, not schedules, and never touch the YAML files.

- **Overlay.** `server/src/retro_cable/catalog/overlay.py`. An `Overlay` holds `channels[]` and `carriage[]` in the same models as `channels.yaml` and `carriage.yaml` (plus `premieres[]` from M2 and `templates[]` from M3, below). The effective catalog is the YAML catalog plus the head overlay (`apply_overlay`), validated by the same cross-file `validate()`. Overlay channel ids start with `custom-`, so they can't collide with YAML ids. In M1, YAML channels are read-only in the admin.
- **Version.** An empty overlay leaves the YAML version unchanged. Otherwise the version is `sha256(yaml version + canonical overlay JSON)[:12]`. A schedule stays a pure function of (channel, date, catalog version, seed), and undo brings back the exact previous version.
- **History.** Migration `0003_catalog_revisions` adds an append-only `catalog_revisions` table (`id`, `created_at`, `parent_id`, `overlay` as canonical JSON, `note`). The row with the highest id is the head.
  - The first save records an empty "start" row, then the new head.
  - Undo appends a copy of the head's parent, with the parent's own parent, so successive undos walk back and rows are never rewritten.
- **Draft to models** (`server/src/retro_cable/admin/drafts.py`):
  - The channel gets one identity from its launch (Jan 1 of the launch year), or from 1990-01-01 when it is library.
  - Each show's carriage runs from Jan 1 of its `from_year` (default: the launch) to Dec 31 of its `until_year` (default: open).
  - Movie channels get a `shuffled` playlist.
  - Custom channels have no bumpers yet (the bumpers table is per channel id).
- **Library.** Shows and movies come from the database, whether carried or not, plus (M4) carried titles the database doesn't know yet, with nothing to play. The demo seed adds three uncarried shows (Recess, Gravity Falls, Over the Garden Wall). `ingest scan` stores every show folder it can read, carried or not (see Phase 4: media below), so the picker offers the whole NAS library. The Plex importer still stores only carried titles.
- **Not in M1:** editing YAML channels, holiday modes per custom channel, per-row on/off toggles, and the nightly YAML export.
- **Pretend premieres (M2).** The overlay also holds `premieres[]`: `Synthetic` entries (`show`, `channel`, `premiere`) for any channel, YAML or custom. `apply_overlay` merges them into `synthetic`, replacing a `synthetic.yaml` entry for the same (show, channel). Canonical JSON sorts them by (channel, show) and leaves the key out when there are none, so overlays saved before M2 keep their version. A custom channel's draft carries each show's `premiere`, saved with the channel and removed with it; a YAML channel's premieres are set one at a time (`PUT /admin/channels/{id}/premieres/{show}`, `api.md`). No migration: they live in the existing `catalog_revisions` overlay JSON, so undo and the catalog version cover them for free (migration 0006 stays unused).

## Implemented (M3: channel builder, Phase 11 part)

`python -m retro_cable.channel_builder propose|check|apply` (`server/src/retro_cable/channel_builder/`). It turns Brandon's show list into a channel proposal for him to review. Applying it changes inputs only (the admin overlay), never schedules. What the channels carry is what the rig converts, so nothing is applied without the explicit `apply` command.

- **Input.** The lineup CSV (columns `action, review, category, title, kind, first_aired, after_2010, original_network, on_nas, already_carried, tmdb_id, aka, notes`). Only `keep` and `add` rows count; a title seen twice keeps its first row. A show keeps one id everywhere: a carried show with the row's TMDB id (M4, below), else a carried catalog title, else a library (database) title, else the slug `ingest scan` gives the folder. `first_aired` may be a date, a year-month or a year.
- **Set aside by code, never sent to the model:**
  - shows some channel already carries (YAML or /admin) are listed as already carried and stay where they are;
  - `special` rows (one-offs, TV movies, miniseries, holiday specials) are unplaced with that reason; the builder doesn't place them yet.
- **What the model decides** (the rig's local model through LiteLLM; `RC_LLM_URL` default `http://rig:4000`, `RC_LLM_KEY`, `RC_LLM_MODEL` default `chat`). Each answer is one JSON object checked against a schema and extra rules. An invalid reply goes back to the model with the problems listed, twice at most; then the code falls back.
  1. *Concepts*, once per group: name, call sign, type (network or marathon; movie for the movie group), a one-line identity and an era. The groups are the list's categories (preschool, kids, family, teen, adult, anime; unknown counts as family), plus all movies as one group. A group asks for about one channel per 30 shows, at most 8. The fallback is one plainly named channel ("Kids Channel").
  2. *Placement*, in batches of 40 shows (`--batch`): one channel each, or two when a show clearly belongs on both. The choices are the group's new channels plus the existing catalog channels of the right kind (movie channels for movies only). For post-2010 shows the model may also suggest a premiere year. Every show key must appear once. A show still unplaced after the retries is listed with the reason.
  3. *Day-parts* for each new network channel with two or more shows: name, `weekdays|weekends|all`, `HH:MM` start, shows, and prime time. The fallback is no day-parts: the default all-day rotation.
- **What the code decides:**
  - *Pretend premieres:* every show that debuted after 2010 gets one on its channel. The year is the model's suggestion if it falls inside 1990–2010, else 20 years before the debut (2014 is 1994), clamped. The date keeps the real month and day (Feb 29 falls back to Feb 28), or is Sep 15 when the list gives only a year. A premiere is never before the channel's launch.
  - *Carriage:* from Jan 1 of the year the show can first air (its debut, or its pretend premiere; 1990 for older shows), never before the launch. Carriage is open-ended. Episodes still wait for their own dates.
  - *Launch:* a new channel launches no later than its earliest show can air. It launches earlier only if the model's era is earlier.
  - *Numbers:* by audience band (preschool 12, kids 20, family 32, teen 50, adult 60, anime 80, movies 90, else 100), taking the next number that no channel uses in any year.
  - *Call signs:* the model's, cleaned to six letters or digits, else the name's initials. A digit is added while a sign collides.
  - *Ids:* `custom-<slug of the name>`.
  - *Day-part templates:* one template per new network channel. It starts on Jan 1 of the first year every part has a show that can air; before then no template covers the channel, so it plays everything it carries all day instead of going Off Air in a part whose shows haven't started (dry run, Nov 2). Every weekday gets a 00:00 part with all of the channel's shows unless the plan has one, and shows the plan leaves out join the 00:00 parts. Unknown show ids are dropped. Marathon and movie channels get no template.
- **Checks: the /admin checks, unchanged** (`check_proposal`). Each new channel becomes a `ChannelDraft` and goes through `check_draft` with its templates: field checks, the catalog's cross-file `validate()` and a week's schedule smoke test. Channels are checked in order, each on top of the ones before it. An existing channel adding shows gets the per-show field checks, `validate()` and the smoke test. A channel with errors is skipped and reported, and the rest still check. Shows not in the server's library yet are checked with the list's debut date, so they warn ("no playable files yet") rather than fail. Carrying them is what makes the rig convert them once they are on the NAS. A further warning flags marathons with more than 6 shows.
- **A shared, busy rig (M4).** Other jobs share the rig's GPU, so the endpoint can time out or answer 429/5xx while it swaps models. Such a call is asked again up to 4 times, waiting 60 s × the attempt number; a 4xx such as a bad key fails at once. Every reply is also appended to `DIR/replies.jsonl`, keyed by a SHA-256 of the chat (replies only, never the key). If the run still stops, `propose … --resume` answers the chats already in that file from it and asks the model only from where it stopped; the prompts are deterministic, so they match. Without `--resume` the file starts empty. (The first full run on Brandon's 410 titles stopped after 55 minutes on one 502 and kept nothing, Nov 7.)
- **Output** (`--out DIR`): `proposal.json` (the `Proposal` model, with the issues), `proposal.md` (channel → shows, day-parts, errors and warnings, unplaced, already carried) and `proposal.csv` (one row per channel and show, plus the unplaced rows). Brandon may edit the JSON before applying it; `check` and `apply` never call the model.
- **Apply** (`apply proposal.json [--skip-invalid]`): re-runs the checks against the database's current head. It refuses when any channel fails, unless `--skip-invalid`, which applies the rest. It saves one overlay revision (note `channel builder: N channels, M shows`), so one undo in /admin reverts the whole proposal. New channels are custom channels like any made in /admin. Shows added to an existing YAML channel are overlay carriage (and premieres) on that channel. The YAML files are never written.
- **Overlay templates (M3).** The overlay also holds `templates[]` (the `Template` model of `templates.yaml`). Only the builder writes them, for its own custom channels. `apply_overlay` appends them to the YAML templates, and `validate()` checks them like YAML ones (pools name carried shows, a 00:00 part per weekday, no overlapping eras). Canonical JSON leaves the key out when there are none, so earlier overlays keep their version. Deleting a custom channel removes its templates.
- **Editing a built channel in /admin** keeps its templates while every show they name is still on the channel. Otherwise the templates are dropped, with a warning, and the channel plays everything all day. Shows the builder placed before they reached the library stay editable; the checks warn that they have no playable files yet.
- **Not yet:** proposals in /admin or Telegram, the weekly station manager, holiday specials and holiday tags, bumper style, `until` years, and a removal list ("take these shows off").
- **Tests.** Every step runs on a fake model (`tests/channel_builder/`, no network). A Hypothesis property applies random proposals that pass the checks. Every applied channel then keeps the scheduler invariants: back to back from 5 a.m. to 5 a.m., nothing before its (pretend) date, programs in full, no channel before its launch, deterministic. A second property checks that a channel with errors is never applied and that numbers stay unique.

## Implemented (M4: the final lineup)

Brandon finished his show list on Nov 7 2026: 410 titles (204 shows, 173 movies, 33 specials) with `keep` or `add`, and those are exactly what he wants on the dial. The channel builder adds shows to channels but never removes any, so the YAML catalog is pruned to the list first, and the list's TMDB ids and first-aired years go into the catalog so `ingest acquire` can find what isn't on the NAS.

- **Prune** (`python -m retro_cable.channel_builder.lineup prune --shows CSV [--catalog DIR] [--same SHOW=TITLE ...] [--keep SHOW ...] [--dry-run]`, `channel_builder/lineup.py`). Each carried show is matched against every row of the list, `remove` rows included:
  1. `--same SHOW=TITLE` (this carried show is that row) and `--keep SHOW` (keep it; no row to take ids from) are judgment calls made by hand, and come first.
  2. *Same title:* a carriage title or `plex_title`, normalized as `ingest scan` compares titles, equals a row's title or `aka` name, with or without a trailing `(1998)`; failing that, the same with spaces ignored (`YuYu Hakusho`). A `keep`/`add` row keeps the show; if only `remove` rows match, it goes.
  3. *Variant:* no row has the title, but exactly one kept row of the same kind (a show; or a movie or special for a movie) starts with it or is its start, word for word (`The Adventures of Jimmy Neutron` and `…, Boy Genius`). Several such rows keep the show without ids (`ambiguous variant`) until `--same` picks one.
  4. Anything else is not in the list and is removed.
- **What it changes.** Removed shows leave `carriage.yaml`, `synthetic.yaml`, `overrides.yaml` (validation requires an override to name a carried show, unless it only sets `tmdb_id`, `year` or `plex_title`: the builder's shows are carried in /admin) and the template pools. Comment headers of sections that lose every entry go too, and blank lines don't pile up. Kept shows get `tmdb_id` and `year` from their row in `overrides.yaml`, added to an existing entry or appended under a `# TMDB ids and first-aired years from Brandon's lineup` header; a value already there wins and the difference is reported. Running it again changes nothing. The result is validated before anything is written: nothing is written if it fails.
- **Templates.** A pool show is *alive* in a template when the channel carries it at some point in the template's years.
  - A part whose pool lost shows and has nothing alive left plays the template's alive shows instead. A part with a channel block keeps the block (an empty pool: everything it carries) while the block still has something alive.
  - A part whose pool lost shows and is left with years in which nothing it names is carried gets the template's shows carried in those years added (weight 1).
  - A template with nothing alive left is dropped, so its channel gets the default all-day rotation.
  - Parts the prune didn't touch are left alone. The report lists every change, plus the template parts and channels that still have years with nothing carried (those hours are Off Air).
- **Channels are never removed**, even with nothing carried, so the builder can still place shows on them. Validation doesn't require carriage.
- **The Nov 7 run.** `--same "digimon=Digimon: Digital Monsters" --keep peanuts-holiday-specials` on `retro-cable-shows-final.csv`: 98 carried titles kept and 177 removed (61 on `remove` rows, 116 not in the list). The judgment calls are in the PR. 313 kept titles aren't carried by any YAML channel; the builder places them. No template was dropped and no channel lost all its carriage (118 carriage rows remain). Of the 98 kept titles, 97 have a `year` and 87 a `tmdb_id` in `overrides.yaml`. The list has no TMDB id for the other 11 (Ed, Edd n Eddy, Halloweentown, Justice League, Looney Tunes, Lupin the 3rd, Star Wars: Clone Wars, Billy & Mandy, Tom and Jerry, Twitches, Zenon), and Peanuts holiday specials has neither.
- **TMDB ids in the catalog.** `overrides.yaml` entries take `tmdb_id` (`tv:N` or `movie:N`, the list's format) and `year` (first aired, or a movie's release). The admin overlay has `ids[]` (`ShowIds`: `show`, `tmdb_id`, `year`). `apply_overlay` fills them into the effective overrides, but only for shows something carries and only where the YAML has no value. Canonical JSON sorts them by show, drops empty fields, and leaves the key out when there are none, so earlier overlays keep their version. Ids belong to shows, not channels: deleting or replacing a custom channel keeps them, and ids of shows nothing carries are ignored.
- **The channel builder and the ids.** A list row whose TMDB id a carried show has in the effective overrides takes that show's id (before the title match), so a title variant isn't placed twice. Placed and already-carried shows keep the row's `tmdb_id` and first-aired `year` in the proposal (`ProposedShow`, `AlreadyCarried`). `apply` writes them to the overlay's `ids[]` where the effective catalog lacks them; malformed ids are left out. `apply proposal.json --shows CSV` fills them into a proposal drafted before they existed. The checks warn when a placed show has the TMDB id of a show some channel already carries (the same show twice?).
- **The admin library** (`GET /admin/library`) now also lists titles a channel carries that the database doesn't know yet (nothing on disk anywhere: `episodes` 0, no dates), with `tmdb_id` and `year` from the effective overrides on every item. The rig's `ingest acquire` reads this list from the mini (`RC_PRIORITY_URL`), so shows the builder places that aren't on the NAS reach it. /admin can carry such a title on a custom channel too; the check warns that it has nothing to play yet.

## Implemented (M5: the weekly station manager)

`python -m retro_cable.station_manager run [--dry-run] [--db] [--catalog] [--weeks 4] [--seed]` (`server/src/retro_cable/station_manager/`), on a timer every Sunday at 2 a.m. New York time (`ops.md`). It reads what was watched (`api.md`, `POST /watch`), turns up the weights of shows Brandon finishes and turns down the ones he skips, and gives each channel he watches a power hour. Inputs only: the admin overlay's `tuning`, never a schedule, the YAML, carriage, dates or holidays.

- **Viewing data.** Migration `0007_watch_sessions`: one row per program watched on a channel (`session`, `device` (NULL until the apps have one), `channel`, `item` (show or movie id), `kind`, `season`, `episode`, `sim_date`, `started_at` (real UTC), `offset_seconds`, `seconds`, `runtime_seconds`, `ended`: `tune_away`, `power_off`, `program_end` or NULL while playing). No IPs, tokens, URLs or paths.
- **Signals** (`signals.py`, deterministic: the same sessions in any order give the same signals). The window is the `--weeks` (default 4) before the run. Sessions under 10 s are channel surfing and count for nothing.
  - per (channel, show): seconds watched, programs, *completion* (seconds watched over seconds available after tuning in, so tuning in halfway and staying to the end counts as finished) and *early tune-aways* (a tune-away under 3 minutes);
  - per channel: seconds, programs and *dwell* (average seconds per program);
  - an hour-of-week *heatmap* of real wall-clock time in the catalog's zone (America/New_York), each session's seconds split across the hours it covers;
  - the simulated year watched most (ties: the later year), where the checks run.
- **Not enough viewing.** Under **2 hours** in the window, the run is recorded as skipped ("Not enough viewing yet: 22 min in 4 weeks (it needs 2 h). Nothing changed.") and the model isn't asked.
- **Knobs.** The weights it may turn: each show's carriage on a channel (`carriage:<channel>:<show>`, every window of it: the all-day rotation, tag pools and empty block parts), and each show's entry in a template's parts that start at one time (`pool:<template>:<HH:MM>:<show>`, the same day-part on every weekday). The value is a *factor* on the catalog's weight (YAML or /admin), so drift is always measured from the channel as programmed and never compounds week to week.
- **What the model decides** (the rig's local model through `channel_builder/llm.py`: `OpenAIChat.from_env`, `ask_json` with two retries on an invalid reply, and the busy-rig retries; never a cloud model). The prompt lists, for each channel watched, its signals, its knobs with their weight and current factor, the allowed factor range and the channel's change limit, plus the power hour's choices. The reply is `{"summary", "changes": [{"knob", "factor", "why"}], "power_hour": [{"channel", "shows": [{"show", "weight"}]}]}`. A reply naming a knob or power-hour show that wasn't offered goes back to the model; numbers are clamped by the code, not retried.
- **What the code enforces** (`tuning.py`), never the model:
  - **Authenticity dial** (`authenticity` on the channel, default 30; 0 = era-accurate, 100 = all favorites) caps the drift: a factor stays within 5^(dial/100) of 1 either way (dial 0: no change; 30: x0.62 to x1.62; 50: x0.45 to x2.24; 100: x0.2 to x5). Factors are rounded to 0.01 and always within 0.2 to 5 (`TuneFactor`).
  - **A fifth per week:** at most a fifth (rounded up) of a channel's knobs change in one run, the biggest moves (by |log(after / before)|) first; the rest are reported as left out.
  - **Power hour:** the busiest hour of the day in the heatmap, widened to two hours when a neighbouring hour has at least half as much viewing; every day (the scheduler's weekdays are the simulated ones, so Brandon's real Tuesday is another weekday in 1995). Per channel it pools at most 4 shows, each one the channel carries that Brandon watched there for 10 minutes or more and finished at least half of, at weights 1 to 3. Only on channels with a dial above 0 that aren't movie channels. Each run's power hours replace the last run's.
  - **Inputs only** (`inputs_only`): after the change the effective catalog has the same carriage rows and windows, the same templates and parts, channels (launches), premieres, overrides (debuts) and holidays; only weights differ. Nothing is carried or dropped, so nothing triggers a download or a delete on the rig.
- **Checks: the /admin ones.** Each channel the run changes is checked on top of the ones before it: `inputs_only`, the catalog's `validate()` and the week's schedule smoke test (`smoke_test`, the week of the run's date in the year watched most). A channel that fails keeps its current tuning and is reported. Then:
  - **applied** (`RC_STATION_AUTO_APPLY=1`, the default): one overlay revision, note `station manager: week of YYYY-MM-DD, N weights on M channels, power hour HH:MM (1 h) on K channels`, undone in one tap in /admin. It is saved only if the head is still the revision the run started from; an /admin edit made meanwhile wins and the run is recorded as failed;
  - **proposed** (`RC_STATION_AUTO_APPLY=0`): recorded only. `python -m retro_cable.station_manager apply RUN_ID` applies it later if the weights haven't changed since and its channels still pass the checks;
  - **dry_run** (`--dry-run`): the whole run, recorded, nothing applied;
  - **skipped**: not enough viewing, or nothing left to change;
  - **failed**: the model unreachable, its replies still invalid after the retries, or the catalog changed during the run. Nothing changes.
- **Runs** (migration `0008_station_runs`): every run with its week (the Sunday it falls in), status, model, the revision it saved, a one-line summary and a JSON report: the signals (the 30 most watched shows, channels, heatmap, busiest hours), the power-hour window, each change with its before and after factors and the model's reason, the power hours, what was left out and why, the model's summary, and the tuning before and after. `GET /admin/station/runs` and the /admin Station tab show them (`api.md`, `web.md`).
- **The overlay's `tuning`** (`Tuning` in `models.py`): `carriage[]` of `CarriageTune` (`channel`, `show`, `factor`), `pools[]` of `PoolTune` (`template`, `start`, `show`, `factor`), `power_hours[]` of `PowerHour` (`channel`, `start`, `hours` 1 to 3, ending by midnight, `pool` of shows). `apply_overlay` multiplies the weights of matching carriage rows and pool entries (YAML or custom), and passes the power hours on channels that exist and aren't movie channels, pooling shows the channel carries, to the scheduler (`Catalog.power_hours`; see `scheduler.md`). Entries that no longer match anything (a deleted custom channel) are ignored. `validate()` checks power hours: a known non-movie channel, one each, carried shows. Canonical JSON sorts the lists and leaves the key out when the tuning is empty, so earlier overlays keep their text and version. /admin channel edits keep the tuning; undo reverts it like any save.
- **Not yet:** likes (hold Select on the info banner); the authenticity dial in /admin (it is `authenticity` in `channels.yaml`, and 30 for custom channels); Telegram reports.
- **Tests.** `tests/station_manager/` on a fake model (no network): signals (completion, early tune-aways, dwell, the heatmap across DST and hour edges, the power window, and a Hypothesis property that signals don't depend on order and add up); runs end to end (skipped, applied within the caps and undone, proposed then `apply`, dry run, model unreachable, invalid replies, a corrected reply, a channel failing the checks, an /admin edit during the run, dial 0, the prompt, the CLI, the admin endpoint); and the Hypothesis property `test_properties.py`: whatever the model proposes, the applied tuning stays inside the guardrails (inputs only, the dial's cap, a fifth per channel, power hour limits) and every channel keeps the scheduler invariants on any day (5 a.m. to 5 a.m. back to back, nothing before its date, programs in full, no channel before its launch, deterministic). `tests/catalog/test_tuning.py` covers the overlay.

## Implemented (Phase 4: media)

### Database

- `0004_media` (after `0003_catalog_revisions`) adds `media_path`, `media_seconds`, `media_source_size` and `media_source_mtime_ns` to `episodes`, `movies` and `bumpers`. `media_path` is the normalized MP4, relative to the media root; `media_seconds` is its ffprobe duration, rounded down, and is the runtime the scheduler uses.
- `0005_source_seconds` adds `source_seconds` to `episodes` and `movies`: the source file's ffprobe duration, rounded down, written by `ingest scan` (and again by `normalize`, which probes the source anyway). NULL until measured. Detaching a file keeps it, so a normalized file whose large source was deleted can still be checked against it.
- `0006_source_deleted` (M4) adds `source_deleted_at` to `episodes` and `movies`: when the rig deleted the row's source (a Live TV download) after a whole, checked encode of it. Such a row has no `file_path` but keeps its encode and `source_seconds`, so it stays in the library, the manifest and the schedule, and `normalize` counts it as done instead of encoding it again. A scan that finds a new source for the row clears it.
- **Real lengths, never a class default** (M1 review, Nov 2 2026: the demo seed's runtime class said 1320 s for Johnny Bravo while the file was a 120 s sample). The length the scheduler uses is `media_seconds` when there is a normalized file, else `source_seconds`, and only else `runtime_seconds` (Plex, TMDB, or the demo seed's runtime class).
- **Short media.** The expected length of a program is the longer of `runtime_seconds` and `source_seconds`. A playable file shorter than half of it is a sample or a truncated encode, and `load_library` leaves it off the schedule, so a 2-minute clip never airs as an episode. With no runtime known, a truncated download measures whole against itself, so an episode under a quarter of its show's median playable length is left off too, once the show has at least five playable episodes (Nov 9 2026: Leverage Redemption S01E13 was a 62 s clip; on the mini it also holds back 15 Animaniacs season-8 shorts of 9 s to 5 min and three specials). `make inventory` doesn't list these. Set `RC_ALLOW_SHORT_MEDIA=1` (API environment) to schedule such files anyway, each at its own measured length, for trials (`normalize --max-seconds`) and demos. `normalize` lists them under `short` and warns on stderr, `make inventory` lists them under "Shorter than half their runtime", and the `/admin` library counts only episodes the schedule can air. Bumpers are not checked.

### Disk scan (`python -m retro_cable.ingest scan --tv DIR [--movies DIR]`)

A stopgap for while there is no Plex token: it reads Plex-named folders straight off the disk.

- **Several roots (M4):** `--tv` and `--movies` may each be given more than once (the rig: Plex's trees, then the Live TV downloads in the LiveTV share). The roots read as one library: a show's folders in every root merge, and when two roots have the same episode (or movie) the earlier root's copy wins, so a re-scan never flips between them. A root that isn't there is skipped, listed under `missing_roots`, and detaches nothing.

- **Matching:** each top-level folder is matched to a carried show with the Plex importer's rules (`plex_title` overrides, `exclude`, case- and punctuation-insensitive, without a trailing `(year)`), then also without spaces (`The OC` = `The O.C.`) and with a trailing article moved to the front (`Grim Adventures of Billy and Mandy, The -`). Synology `@eaDir`, `#recycle` and dot-files are skipped.
- **Episode numbers:** `S01E02` (also `S1E2`, `S01E01-E02`, which records the first number), `1x02`, `Season 1 Episode 2` or `S1Episode 2`, a leading `1.02 Title` or `5.1 Title` (a one-digit episode only before a space, so `14.5. Commentary` is no episode; never episode 0), and inside a `Season N` folder `102 - Title` or `02. Title`, also one release folder down (`Season 1/Gravity Falls 720p/01 Tourist Trapped`, unless that folder is an extras folder). A release tag may follow the season number (`Season 1.AAC`, `Season 2 [DVDRip]`, a trailing space), but not another number (`Season 1 - 2` is not a season folder). Outside a season folder, `102 - Title` or `Show - 102 - Title` is S01E02 when every such numbered file in that folder has a season digit and an episode from 01 to 49 and there are at least two; lower numbers in the same folder (`099`, `100`) mean absolute numbering, so the folder stays unparsed (Freakazoid, What's New Scooby-Doo, Nov 9 2026). Absolute numbering is read when three or more files in a folder have one name with a different number in each, tags in brackets aside (`[a-s] Samurai Champloo 01`, `Princess.Jellyfish.-.02.-.1080p`): each is season 1, episode N, untitled. Not in an extras folder, nor in one that names a season (`Series 5 + Bonus/Episode 01`, `Dog.S01/cd101`), and names that differ in more than the number (`FMA Brotherhood - 28 - Father`) stay unparsed (Samurai Champloo and Gravity Falls season 1 were unread, Nov 9 2026). `Specials` is season 0. Files without a number are listed as unparsed; trailers, samples and extras are ignored.
- **Uncarried shows:** a folder that matches no carried show is stored too, as a show no channel carries, so `/admin` can build channels from it (`--carried-only` turns this off). Its title is the folder name without the year, release tags (`S01E01`, `Complete Season 1`, `720p`, `x265`...) or Synology's private-use stand-in characters, with dots read as spaces when there are no spaces and a trailing article moved to the front (`Office, The (2005)` is `The Office`, `Russian.Doll.S01E01.720p.HEVC.x265-MeGusta` is `Russian Doll`); its id is the title's slug. Folders with the same title and the same `(year)` in the name (or none) are one show, so release folders merge while remakes such as `Doctor Who (1963)` and `Doctor Who (2005)` stay apart. If the slug is taken (by a catalog id, another show, or another folder this run) it takes the year (`doctor-who-1963`, titled `Doctor Who (1963)`), else a number. A re-scan reuses the id an earlier scan gave the folder, so admin channels that carry it keep working. Titles `overrides.yaml` excludes are skipped, and folders with no numbered episode are listed as unmatched. Its air dates follow the undated rules in `scheduler.md`: from Dec 31 of the folder year, or none (library channels only) without one. When a folder later matches a carried show, its files move to the carried id and the old uncarried row keeps no files.
- **Duplicates:** one file per episode, preferring an earlier root's copy, then a copy inside a `Season NN` folder, then the bigger file.
- **What it stores:** the carriage title, the premiere year (from `(Year)` in the folder, else the commonest `Show (Year) - Sxx` file name), and per episode the title from the file name, the absolute file path and size, and the file's ffprobe length (`source_seconds`). It never writes air dates, and keeps titles, dates and runtimes that Plex or TMDB already set.
- **Measuring:** each new or changed file (different path or size), or one not measured yet, is read with ffprobe; unchanged files keep their recorded length, so a re-scan of the NAS probes only what changed. A file ffprobe can't read gets no length. `--no-probe` skips measuring.
- **Re-runs:** files under a scanned root that are gone, or now belong to another show, are detached (`file_path` cleared); rows outside the scanned roots, under a missing root, or whose source was deleted after its encode (`source_deleted_at`) are untouched. Finding a new file for a row clears `source_deleted_at`.
- **Movies:** `<root>/<Title (Year)>/`, the biggest video that isn't a trailer or extra (from the earliest root that has one), with the year and no premiere date. Release-named folders match too (Nov 8 2026): the title ends at a bracketed year, a bare year up to 2029 followed by a release tag or the end, or the first release tag (`Psych.The.Movie.2017.HDRip.XviD.AC3-EVO` is `Psych The Movie`; `Blade Runner 2049` stays whole). With `--priority-url` (the rig's `RC_PRIORITY_URL`) the movies on the mini's `/admin` channels are carried too, under the mini's ids; before, the 21 builder-placed movies already on the NAS went unmatched and were sent to radarr-livetv.
- Each run is recorded in `import_runs` (`scan`, `scan-movies`).
- On the NAS (2026-11-02) it matches 21 carried shows in `/volume3/tv` and finds 2,968 episodes. With uncarried shows (checked against a mirror of the NAS file names) it stores 141 shows: 21 carried with 2,980 episodes and 120 uncarried from 123 folders with 5,434 episodes; 3 folders have no numbered episode, and a re-scan keeps every id. `Justice League (Unlimited)` needs a `plex_title` override to match `justice-league`.

### Normalize (`python -m retro_cable.ingest normalize`)

- **What:** by default the episodes of shows some channel carries, in the YAML catalog or on a custom channel in the head `/admin` overlay, plus the movies some channel carries and every bumper. With `--priority-url` (the mini's `/api/admin/library`) it also takes the shows on channels made in the mini's `/admin`. `--all` also encodes uncarried shows and movies (the whole NAS TV tree is about 3x the carried set); the rig doesn't use it. So a show added to a custom channel plays after the next normalize run.
- **Output:** one MP4 per episode, movie and bumper with a source file, at `tv/<show>/sNN/<show>-sNNeNN.mp4`, `movies/<id>.mp4` or `bumpers/<channel>/<name>.mp4` under `--media-dir` (`RC_MEDIA_DIR`).
- **Encoding:** H.264 High, yuv420p, CRF 23 capped at 1.5 Mbit/s; AAC stereo 128 kbit/s at 48 kHz (default; `--codec hevc` or `RC_CODEC=hevc`, the rig: HEVC Main tagged `hvc1`, capped at 1 Mbit/s, AAC 96 kbit/s, see the ops spec for the trial) from the English audio track if tagged, else the first; no subtitles, data or metadata. A keyframe every second (`-force_key_frames`), and the index at the front (`+faststart`).
- **Picture:** deinterlaced when frames are flagged interlaced (`bwdif`), at most 480 lines, never upscaled, square pixels with the display aspect kept (anamorphic sources are corrected). A picture within 3% of 4:3 or 16:9 is drawn at exactly that shape. With `--crop auto` (default), black bars baked into the picture are cropped first: cropdetect samples 4 s at 20%, 50% and 80% of the running time, the crop is the union of the three boxes, and bars under 2.5% a side are left alone. This turns 4:3 shows released in 16:9 frames (The Simpsons and Courage web releases) back into 640×480.
- **Encoders:** `--encoder x264` (libx264 `veryfast`, default) or `--encoder nvenc` (`h264_nvenc`, preset p5, VBR CQ 23, forced IDR keyframes); also `RC_ENCODER`. With `--codec hevc` they are `libx265` (`fast`, CRF 22) and `hevc_nvenc` (p7, `-tune hq`, VBR CQ 26, spatial and temporal AQ). `--encoder auto` picks per item: NVENC while the shared GPU is idle, x264 otherwise (`ingest/gpu.py`; see the ops spec, "Media worker (rig)"). The run's `encoders` stats count encodes per encoder.
- **Order:** `--order catalog` (default: show, season, episode, then movies and bumpers) or `--order priority` (the rig: bumpers, then carried shows by earliest carriage year in waves of 13 episodes, then uncarried shows; `--priority-url` adds the mini's `/admin` channels).
- **Manifest:** `--manifest` writes a JSON sidecar next to each new MP4 and `manifest.json` at the media root (every 5 minutes and at the end), which the mini's `ingest index-media` reads instead of probing files (ops spec).
- **Sources:** absolute paths (as Plex or `scan` saw them) go through `--source-map FROM=TO` (or `RC_SOURCE_MAP`, comma-separated); relative ones resolve under `--source-root` (`RC_SOURCE_ROOT`). A missing source is listed and skipped.
- **Idempotent and resumable:** an item is skipped when its output exists, the source size and mtime match what was recorded, and the recorded encode length is within 2% of the recorded source length (M2: an encode recorded short is converted again); `--force` re-encodes. Each encode goes to `<name>.partial`, is read back with ffprobe, renamed, and committed on its own, so an interrupted run leaves no half files and the next run carries on. A failed item is listed and the run continues.
- **Whole encodes only (M2 B11):** the read-back must show a video stream, an audio stream when the source has one, and a length within 2% of the source's ffprobe length (at least 1 s of slack, for bumpers). The source's length is the longer of the video and the kept audio stream (`duration`, or MKV's `DURATION` tag), capped at the container's: a subtitle track that runs past the end doesn't count (Cougar Town S01E01's ran 100 s over and its whole encode was refused, Nov 9 2026). When either stream doesn't say, the container's length is used, or of `--max-seconds` for a trial. Anything else is a failure: the partial file is deleted, nothing is recorded, the reason goes to the log, and the next run tries again. A trial encode is not whole for a full run, so a later run without `--max-seconds` converts it again.
- **Trials:** `--limit N` encodes at most N items, `--max-seconds S` keeps the first S seconds, `--dry-run` prints the ffmpeg commands. A `--max-seconds` encode is short media (see Database above): it is recorded but stays off the air unless `RC_ALLOW_SHORT_MEDIA=1`.
- **Lengths:** each item records the encode's length (`media_seconds`) and the source's (`source_seconds`, episodes and movies). An encode shorter than half the expected runtime is listed in the run's `short` stats as `show S01E02 (120 s of 1320 s)`.
- **Deleting originals (M4, `--delete-sources-under DIR` or `RC_DELETE_SOURCES_UNDER`; unset, the default, never deletes):** after an episode's or movie's encode is recorded, or on a later pass for one recorded earlier, its source is deleted when all of these hold: both lengths are known and the encode is whole (within 2%) and not short; its MP4 is in the media root; the source's size and mtime are still the ones it was encoded from (a file replaced during the encode is kept); the source path is not a symlink and its real path is a regular file strictly inside the real path of DIR (no `..`, no symlinked folder out of it). Never in a `--dry-run` or a `--max-seconds` trial, and never a bumper. The row then loses `file_path` and gets `source_deleted_at` (see Database); the run lists the deleted files under `deleted`, relative to DIR, and logs each one the same way. Such an item is skipped on later runs (counted as done in `progress.json`), or reported missing if its MP4 is gone too.
