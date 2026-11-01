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
