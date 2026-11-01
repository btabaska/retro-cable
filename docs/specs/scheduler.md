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
