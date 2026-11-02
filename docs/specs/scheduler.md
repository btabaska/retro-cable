# Scheduler spec

Split from `docs/plan.md` (approved Nov 1, 2026) without changing its meaning; plan text is copied verbatim. Where this spec and the plan disagree, this spec wins for its area. Related specs: `catalog.md`, `api.md`, `web.md`, `ops.md`.

## Decisions locked

| Area | Decision |
| --- | --- |
| Backend | Custom scheduling engine; open-source pieces reused only where they win |
| Year range | 1990–2010, set in the UI |
| Clock | Same calendar date and time of day in the chosen year; that date's weekday in that year sets the programming (Oct 31, 1998 was a Saturday, Oct 31, 1997 a Friday) |
| Episode eligibility | Anything that had aired by the simulated date: new episodes plus reruns |
| Between shows | Back-to-back programs with one short bumper or promo between, no commercials |
| Channel existence | Hidden before launch (Cartoon Network 1992, Toonami 1997, Adult Swim 2001); Boomerang always shown |

## Scheduling engine

The schedule is a pure function of (channel, simulated date, catalog version, seed), so nothing streams until you tune in and changing the year is instant.

### Simulated clock

- Simulated time = chosen year + today's month and day + the current local wall-clock time. Feb 29 falls back to Feb 28 in non-leap years.
- The simulated date's own weekday picks the day-part template (Oct 31, 1997 runs a Friday schedule).

### Channels and carriage

- Each channel has year-ranged identity: number, call sign, logo and bumper set. Example: NBC is KRON 4 through 2001 and KNTV 11 from 2002.
- Each channel has a launch date and is hidden before it (Boomerang excepted).
- Each show has carriage windows per channel, so Futurama can be on Fox 1999–2003 and on Adult Swim from 2003.

### Day-part templates

- Each channel has YAML templates of day-parts per weekday type: weekday mornings, after school, primetime, late night, Saturday morning, Sunday.
- Templates have era versions (Cartoon Network 1992–96 leans on classics; 1997+ adds originals). Claude drafts them; you edit the YAML.
- A day-part names a weighted pool (specific shows or tags like `nick-live-action`), not fixed episodes.

### Episode eligibility and order

- An episode is eligible when its first US air date is on or before the simulated date. Anime and imports use their US debut, set by per-show overrides (Dragon Ball Z counts from its US run, not its 1989 Japanese premiere).
- Each (channel, show) walks its eligible episodes in original air order. The position is computed, not stored: items elapsed since an anchor date, plus a seeded offset, modulo the eligible count.
- If a show premiered an episode that same week on that weekday, its primetime slot airs that one and the guide marks it NEW. Everything else is a rerun.
- An episode missing from the NAS is skipped; a show with no files leaves the pool and is listed in the gap report.

### Holidays, specials and movies

- Episodes carry holiday tags. Tags come from metadata keywords plus a classification pass at ingest, with a manual override file.
- Inside a holiday window (e.g. Oct 24–31), a show's tagged episodes are preferred, and specials are injected into prime day-parts.
- Movies and specials run their full length back to back and respect their premiere date (Halloweentown can't air before Oct 1998).

### Back-to-back programming

- Programs run back to back with no ads, so start times are real: 8:00, 8:22, 8:45.
- One short bumper or promo for a later show plays between programs; none if the channel's pool is empty.
- Day-parts anchor the day: when primetime begins at 8:00, the program already running finishes first, so primetime may start at 8:04.
- The guide draws each program at its true length, so a 22-minute episode is a narrower cell than a 44-minute one.

### Tuning in

The `/tune` endpoint is specified in `api.md` (Tuning in).

## Testing

| Layer | Tests | Runs |
| --- | --- | --- |
| Scheduler invariants | Hypothesis property tests: no episode before its debut; no hidden channel; no gaps or overlaps; deterministic; midnight and DST safe; holiday windows pull only tagged items | Every PR |
| Scheduler golden days | Snapshots for Oct 31 1998, Dec 24 2003, Sep 2 2001 (Adult Swim launch), Jan 1 2002 (NBC switch) | Every PR |

## Implemented (Phase 3)

The engine is `server/src/retro_cable/scheduler/`. `Scheduler(catalog, library, seed)` memoizes days; nothing is stored.

- **Broadcast day.** Each channel's day runs 5:00 a.m. to 5:00 a.m. local. The program running at midnight carries over with no special case, and each day is built from scratch, so a day never depends on the one before it. This is a refinement of the plan, which doesn't say where a day starts.
- **Closing the day.** Near 5 a.m. only programs that fit before 5 a.m. are picked. Any time left becomes a `filler` slot titled "Paid Programming", so every day ends exactly at 5 a.m.
- **Day-parts.** At each step the engine finds the part active at that local time: the covering template's parts for that weekday, latest `start` at or before now. With no template, or no parts for that weekday, it uses the default all-day pool. A part starts only when the running program ends, so primetime may begin at 8:04.
- **Pools.** Show entries match active carriage on the channel. Tag entries match carriage tags, weighted by entry weight × carriage weight. A part with a `block` draws only from that block, and an empty block pool means everything the block carries. The default pool is every carried item outside a block, at carriage weights. Shows excluded in `overrides.yaml` never air.
- **Empty pool.** When nothing in the pool is eligible, the engine runs an "Off Air" filler until the next part, or midnight.
- **Picking.** A seeded weighted choice. The seed is `seed:channel:slot start`. The previous show gets a quarter of its weight when there are alternatives. Ordered movie channels walk their carriage list instead.
- **Episode order.** Each show walks its eligible episodes in air order. The index is (stable per-show offset + days since 1990-01-01 × 3 + airings so far today) mod the eligible count.
- **Eligibility.** An episode needs an effective air date on or before the slot's local date. `overrides.yaml` and `synthetic.yaml` shifts apply at read time. Library channels and library shows make every episode eligible. Movies always respect their premiere date; a movie known only by its year premieres on Dec 31 of that year. Only playable files with a known runtime are scheduled: a normalized MP4 (`media_path`, at its ffprobe length) or a relative file already under the media root. A raw source with an absolute path waits until `ingest normalize` has encoded it.
- **Undated episodes** (from `ingest scan`, or Plex without dates) get a conservative synthesized date, so "no episode before its debut" still holds. The bound is the first that exists: the show's `synthetic.yaml` premiere or `overrides.yaml` US debut; Dec 31 of its premiere year (Plex year, or the `(Year)` in its folder or file names); Dec 31 of the year it was first carried on a non-library channel. Season *s*, episode *e* then airs from bound + (*s*−1) × 365 days + (*e*−1) × 16 days, and never before an earlier episode's date. Real seasons run about weekly from the fall, so this lands on or after the real date for typical shows, at the cost of airing some episodes later than they really did. Specials (season 0 or episode 0) and seasons numbered by year stay undated and don't air. A show with none of the three bounds stays unscheduled. Real dates from Plex, TMDB or `overrides.yaml` `episodes` always win. See `synthesize_undated` in `catalog/air_dates.py`.
- **NEW.** In a `primetime` part, an episode whose effective air date is the slot's date airs first and is marked `new`.
- **Bumpers.** One bumper between programs, chosen at random from the channel's bumpers and capped at `bumper_seconds`. It is dropped when it would push a program past 5 a.m. Bumpers come from the `bumpers` table (migration 0002).
- **Time.** All instants are UTC internally, so durations are real elapsed time across DST. Local time is used only to find the day-part and the simulated date.
- **Not yet:** holiday windows and specials (Phase 7).
