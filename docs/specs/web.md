# Web spec

Split from `docs/plan.md` (approved Nov 1, 2026) without changing its meaning; plan text is copied verbatim. Where this spec and the plan disagree, this spec wins for its area. Related specs: `scheduler.md`, `catalog.md`, `api.md`, `ops.md`.

## Decisions locked

| Area | Decision |
| --- | --- |
| Frontend | Browser app first (same TV interface), then a native tvOS app (SwiftUI + AVKit) |
| Guide | One 2000s digital-cable grid style for every year |

## Frontend: browser first, then tvOS

The first frontend is a browser app with the same TV interface, so agents can test every change in a headless browser and you can try it on any screen within minutes. The tvOS app follows once the browser app is right; both use the same API and design tokens. Mockups are in `docs/mockups/` (open the .html files or view the .png screenshots).

### Browser app (Phases 1–8)

- TypeScript, React and Vite, built to static files that Caddy serves at `tv.home.tabaska.us`; the admin panel lives in the same app under `/admin` with a phone layout.
- A 1920×1080 "10-foot" layout scaled to the window, so a laptop, a TV browser or your phone all show the cable-box screens.
- Arrow keys, Enter and Esc mirror the Siri Remote through a spatial-navigation layer, so focus moves cell to cell as it will on tvOS.
- Playback is a native `<video>` element on the normalized MP4s, starting at the offset `/tune` returns.
- The API client is generated from the OpenAPI spec, the same contract the tvOS app will use.
- Design tokens (colors, type sizes, spacing) live in `design/tokens.json`: CSS variables now, Swift constants later.

### tvOS app (Phase 9)

| Screen | What it does | Remote |
| --- | --- | --- |
| Live TV (launch screen) | Full-screen video, 4:3 pillarboxed; info banner for 4 s on every tune | Swipe or click up/down = channel up/down; Play/Pause = last channel; hold Select = like |
| Info banner | Channel number, call sign, title, episode, progress bar, next program | Click center to open the guide |
| Guide | 2000s grid: about 7 channels × 90 minutes visible, cells sized to real run times, scrolls 12 h ahead, details panel and live picture-in-picture | Select = tune; Play/Pause = year picker |
| Year picker | 1990–2010 grid plus All Years, shows the simulated date and which channels are on the dial | Select = set year and retune |
| No signal | Era-style card when the server is unreachable, retrying in the background | — |

- SwiftUI + AVKit, ported screen by screen from the browser app, which serves as the working reference.
- The guide grid is a custom focusable layout, not a `List`, so focus moves like a cable box.
- Target channel-change time under 1.5 s on both frontends.

### Admin panel

- A phone-friendly web admin at `/admin` on the LAN: channels, shows on or off per channel, carriage windows, synthetic dates, holidays, movie playlists, AI proposals, device pairing.
- Every change previews a week of the guide and runs the scheduler checks before it saves.
- Changes are versioned with one-tap undo, and a nightly job commits them to the repo as YAML.

## Testing

| Layer | Tests | Runs |
| --- | --- | --- |
| Web | Vitest units; Playwright flow: launch → banner → guide → tune → change year | Every PR (Chromium), Mac (WebKit + Chrome) |
| tvOS (Phase 9) | Unit tests and XCUITest for the same flow | Mac, tvOS Simulator |

## Implemented (Phase 1)

- `web/` is Vite + React + TypeScript (strict, including `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`).
- **Stage**: every screen renders into a fixed 1920×1080 stage (`src/Stage.tsx`), scaled with a CSS transform to fit the window and centered, keeping 16:9 (letterbox or pillarbox).
- **Design tokens**: `design/tokens.json` is flattened into CSS variables named `--rc-<group>-<name>` in kebab case (`color.textMuted` → `--rc-color-text-muted`); numbers get `px`. Keys starting with `$` are metadata and skipped. Colors come from `docs/mockups/`.
- **Fonts**: Arimo and Archivo Narrow are bundled from `@fontsource` packages, not loaded from Google Fonts, so the app makes no external calls.
- **Focus**: `moveFocus` (`src/lib/focus.ts`) maps arrow keys to the next grid cell and clamps at edges, as a cable box does. The "hello" screen uses it for one row of three placeholder cells; the focused cell uses the `focus` color.
- **API client**: types are generated from `server/openapi.json` into `src/api/generated/` by `openapi-typescript` and called through `openapi-fetch` at `/api`. Never hand-edit the generated folder.
- **Server unreachable**: the hello screen shows "No signal" where the version would be.
- **Tests**: Vitest + Testing Library for units (`src/**/*.test.ts(x)`); Playwright in `web/e2e/`. Without `BASE_URL`, Playwright starts the API and a production build itself; with `BASE_URL` it runs against a deployed stack (the local-test job uses the compose test stack). Cloud CI runs Chromium; the Mac runs Chromium and WebKit (Playwright's Chromium stands in for Chrome).

## Implemented (Phases 5–6)

- **Power on.** The app opens on a "turn on the TV" screen that shows the server version. The key or click that turns it on is the user gesture the browser needs to play sound.
- **Live TV.** The video is a full-screen `<video>`, 4:3 pillarboxed. The player seeks to (simulated now − slot start) once metadata loads. It then advances through the `/tune` queue on schedule (a timer at slot end) and refetches before the queue runs out. Filler shows color bars and its title.
- **Programs are never cut.** The server sizes every slot to the program's full runtime, so the slot-end timer never truncates a program. If the file is still playing at slot end and has at most 5 s left (`OVERRUN_TOLERANCE_MS`, seek rounding or buffering drift), the player lets it finish, then advances. When a file's real duration differs from its slot by more than 3 s (`DURATION_MISMATCH_S`), the player reports it with `console.warn` (title, episode and seconds; never the media URL) and marks the player `data-duration-mismatch` with the difference in seconds; it does not cut or stretch the program to hide the mismatch. A larger overrun still follows the schedule, so the channel stays in step with the guide; the fix is the slot length (`media_seconds`), not the player.
- **Demo slate.** Demo feeds are generic color bars, so the player overlays the show, season, episode and title. During bumpers it shows the call sign and "Up next".
- **Live TV keys.** ▲ / ▼ (or Page Up / Down) change channel, wrapping and skipping channels not on the dial. Space or P goes to the last channel. Digits tune by number. Enter opens the guide, Y opens the year picker, and Esc, I or ◀ / ▶ show the banner. The info banner shows for 4 s on every tune, with the clock and simulated date. The 4 s run on the real wall clock (`Date.now()`), never on simulated time: simulated now is years behind the wall clock, so comparing the two would leave the banner up forever (M1 review bug). Everything shown inside the banner (clock, progress, start and end times) stays on simulated time.
- **Guide.** One request covers 12 hours from the current half hour. The grid shows 7 rows × 90 minutes. Cells are sized to real run times, and each bumper is folded into the cell before the next program. A cell that started before the window reads "from 7:52". Up and down keep the time anchor. Left and right move by cell and scroll the window by half hours. Enter tunes, Space opens the year picker, and Esc goes back. The details panel shows the focused cell, and the live picture plays as picture-in-picture.
- **Year picker.** A 7 × 3 grid of years. The panel shows the dial for the focused year, with hidden channels marked "Hidden until …". A failed lineup request is retried after 1 s, then 2 s and so on up to 5 s, so the dial never stays empty. Enter sets the year, retunes and returns to live TV. The year and channel are remembered in `localStorage`.
- **Tests.** Vitest covers guide layout, channel stepping, time formatting and the app flow on a fake API. The Playwright flow `e2e/tv.spec.ts` runs launch → banner → guide → tune → change year, checks that the player is past the offset `/tune` returned, and checks that the banner and clock hide about 4 s after a tune and come back on I. Playwright starts the API through `tools/e2e-api.sh` on the demo database.

## Implemented (Phase 7: Feel)

- **Guide cell kicker.** After the start time: `NEW` in the focus gold, bold; `MOVIE` for movies; and (until M2) the holiday name in `color.holiday`, bold and upper case (`8:00 · NEW · HALLOWEEN`); since M2 a holiday cell has a left bar instead (below). On a focused cell the kicker uses `onFocus`. The words are always text, never color alone.
- **Descriptions (Nov 11 2026).** The info banner shows the slot's `description` under the title row, in the small muted type, two lines at most (the banner grows from its 190 px minimum); not for bumpers, and no line when it is empty. The guide details panel's italic line shows the description when there is one, else the episode label as before, also two lines at most.
- **Badges.** The info banner and the guide details show `NEW` (`.badge-new`) and the holiday (`.badge-holiday`: 2px holiday-color border and text, no fill). The details pane also shows a `MOVIE` outline badge. Bumpers and reruns never get NEW.
- **Movies.** The line under the title reads the premiere year and running time ("1998 · 1h 25m") instead of season and episode (`src/lib/program.ts`). Cells keep their true length.
- **Bumpers without media** show a station ID slate on `color.background`: number and call sign, channel name, and "Up next 8:27 Seinfeld". Bumpers with media play them.
- **Tests.** `program.test.ts` (kicker and labels), `InfoBanner.test.tsx` (badges, movie line) and `Player.test.tsx` (bumper slate).

## Implemented (Phase 10: /admin)

- **Route.** `/admin` (and anything under it) renders the phone admin (`src/admin/`) instead of the TV. Production Caddy already serves `index.html` for unknown paths.
- **Tokens.** `design/tokens.json` has an `admin` group, which becomes `--rc-admin-*`. `$`-prefixed keys inside a group are skipped.
- **Shell.** A 64 px ink header, a scrolling body on `admin.bg` with a 16 px gutter, and a 64 px tab bar. In M1 the tab bar shows only Channels; Holidays, Manager and Build are hidden.
- **Channel list.** 48 px rows: number, name, and meta ("Network · 12 shows", "Library · 2 shows", "from 2001 · …"). Custom channels have a solid border and "Custom ·" in the meta; the dashed draft border is for unsaved drafts only (M2). A row opens the editor; YAML channels open read-only. Loading shows 6 skeleton rows, and an error shows a card with Retry. "+ New channel" is gold.
- **Editor.**
  - While an existing channel loads, the header shows its name from the list and the body shows skeleton fields and rows: no values, no shows message, no footer. A failed load shows a card with Retry. The title never falls back to a placeholder like "Channel".
  - Fields: number (`inputmode=numeric`, with the next free number as placeholder), call sign (upper case, max 6), name, type, "On the dial from" year, and a Library switch ("Available in every year").
  - Shows list: tap a row to set from/until years, a pretend premiere, or "Remove from channel". On a YAML (read-only) channel rows are plain, with no chevron and no button (M2).
  - Sticky footer: "Preview week" and gold Save. Save is enabled once the draft changed and has a number, a name and a show, and reads "Checking…" while the server checks.
  - Failed checks show a summary card that takes focus and links to each field, plus inline messages tied to the field with `aria-invalid` and `aria-describedby`. Warnings show in a `warn` card.
  - Delete (custom channels) and leaving with unsaved changes both ask first.
- **Show picker.** A full-height sheet with an autofocused search. Results are grouped "Not on any channel", then "On other channels" ("on 27 Cartoon Network"). Shows already on the channel show ✓ and are disabled. Empty states: "No shows match '…'" and "Your library is empty. Run an import first."
- **Week preview.** A sheet with a year select and Sun–Sat day chips (today's date selected). It lists the day's programs with time and title, folds bumpers away, and shows NEW and holiday chips in the TV colors at 11 px. Failed checks show in place of the list.
- **Undo toast.** After a save or delete, an ink toast appears above the tab bar with a gold Undo. It lasts 10 s, pauses while hovered or focused, and has `role=status`. Undo shows "Restored".
- **Sheets** trap focus, close on Esc, return focus to the opener (read while rendering, before an autofocused child takes it; if the opener is gone, the screen's title), and skip the slide animation under reduced motion. Tap targets are at least 44 px. `:focus-visible` is a 3 px accent outline.
- **Fix.** Number entry on live TV never committed: the commit timer depended on `props` and restarted on every 250 ms clock render. It now depends only on the digits and the dial.
- **Tests.** Vitest: `admin/draft.test.ts`, `admin/AdminApp.test.tsx` (list, create with picker, preview, save, undo, inline errors, discard, retry, editor loading and retry), `screens/YearPicker.test.tsx` (dial, lineup retry), and `lib/route.test.ts`. Playwright `e2e/admin.spec.ts` runs at 390 px: create a library channel from two uncarried shows, preview it, save, tune it by number on the TV and find it in the guide, then undo.

## Implemented (M2: pretend premieres, transplant mark, Media tab, M1 design review)

- **Pretend premiere (custom channels).** Inside an expanded show row, under From and Until year: a full-width "Pretend premiere" `<input type=date min=1990-01-01 max=2010-12-31>`, a Clear text button once set, and the hint "Air this show on this channel as if it first aired on this date. Episodes keep their real spacing. Other channels are not affected." It saves with the channel (`DraftShow.premiere`). Below it: "S1E1 airs Sep 7, 2004 · last episode Mar 2006" (the channel's current run shifted to the date). Errors come back on `shows.<i>.premiere` and link to the field; the row opens before it takes focus.
- **Pretend premiere (YAML channels).** The channel stays read-only, but each show row has a compact Pretend premiere field with Set and Clear that saves at once through `PUT /admin/channels/{id}/premieres/{show}`, shows the undo toast and updates the row in place. Refusals show inline (`role=alert`). A premiere set in `synthetic.yaml` is shown but not editable.
- **Row meta.** Once set: "◇ Pretend premiere Sep 2004 · really Jun 2012", the ◇ a CSS diamond in `--rc-admin-transplant`. A show whose real debut is after its last carried year (or 2010) and has no premiere is flagged: "Debuts Jun 2012 · won't air. Set a pretend premiere." in `--rc-admin-warn` with a hollow diamond and a 4 px warn bar; opening it focuses the date field.
- **Transplant mark on the TV.** A 9 px hollow diamond (2 px border, rotated 45°) drawn in CSS (`.transplant-mark`), never the "◇" glyph, in `--rc-color-transplant` (`--rc-color-on-focus` on a focused cell): before the title in grid cells, and as a "PRETEND PREMIERE" outline badge in the details panel and, after the holiday badge, in the info banner. The details line reads "first aired … (pretend)". Only slots with `transplant: true` get it; the server sets it per channel.
- **Holiday labels** use `--rc-color-holiday-muted` (#E0A458, about 5:1 on a cell): the badges in the details panel and banner, the admin preview chip, and a 3 px inset bar on holiday cells (`--rc-color-on-focus` when focused). The "· HALLOWEEN" kicker is gone from grid cells.
- **Narrow cells.** `cellSize` (`lib/guide.ts`) measures a cell against the 1526 stage-px cell area: under 120 px a cell drops its time and kicker and keeps the ellipsised title (`cell-narrow`); under 40 px it shows no text and `aria-label` carries the title (`cell-sliver`). The details panel always shows the full program.
- **Year picker focus.** The picker focuses the current year on open; closing it (Back or Esc) hands focus back to the opener, found again by `data-focus-key` (the banner's Year button, which is brought back with the banner).
- **Stale errors.** Editing a field clears its errors and those inside it; the summary goes when none are left. Warnings clear at the start of each save and after a successful one.
- **Day chips** are a 7-column grid with two-line labels ("Sun" over "29"), 52 px tall, so a week fits at 360 px.
- **Focus after a screen change.** After a save, delete, discard or tab change, focus moves to the new screen's `h1.adm-title` (`tabIndex=-1`).
- **Media tab.** A second tab, Media, polls `GET /api/admin/media/progress` every 10 s (proposed shape in `api.md`): a card with the percentage, "1,240 of 2,000 files · about 6 h 10 m left" ("Estimating…" until 10 files are done; rolling average of the last 20, rounded to 10 minutes), an 8 px bar and the encoder ("Encoding on GPU", "Encoding on CPU (GPU busy)", "Paused"), then one row per show: title, a 4 px bar, `38 / 52`, failures in `--rc-admin-error`; converting first, then queued, then done (muted, "✓ 52 / 52"). Empty: "Nothing to convert."; 404: "Conversion status isn't available on this server yet." No paths, hosts or URLs.
- **Tokens added:** `color.transplant` (#7FD1FF), `color.holidayMuted` (#E0A458), `admin.transplant` (#2B8FC4).
- **Tests.** Vitest: `admin/AdminApp.test.tsx` (premiere on a YAML row, refusal inline, flag, custom-channel premiere saved with the draft, stale errors, error link opens the row, focus return and title focus, solid custom border, two-line chips, Media tab states), `App.test.tsx` (year picker focus return; transplant mark, sliver cell and holiday bar in the guide), `InfoBanner.test.tsx`, `lib/guide.test.ts` (`cellSize`), `lib/program.test.ts`. Playwright `e2e/admin.spec.ts`: the library-channel flow now gives Gravity Falls (2012) a 1999 pretend premiere; a YAML channel premiere shows the mark in `/guide` and undoes; the Media tab; chips at 360 px.

## Implemented (M5: viewing reports and the Station tab)

- **Viewing reports** (`src/lib/watch.ts`, `hooks/useWatchReporter.ts`). While the TV plays a program on a channel, full screen or in the guide's PiP, a `WatchReporter` keeps one session for it: a random id, the channel, show, season and episode, the year, how far in it was tuned (`offset`, from the simulated now and the slot start) and the runtime. It reports to `POST /api/watch` (`api.md`, M5) on a heartbeat every 60 s; when the page is hidden (`visibilitychange`, by `sendBeacon`); when the next program starts on the same channel (`program_end`); on a channel or year change, or when tuning or the year picker hides the player (`tune_away`); and on `pagehide` (`power_off`, by `sendBeacon`). Seconds are real wall-clock time, capped at the runtime. Sessions under 10 s (flipping past a channel) are never sent; bumpers and filler aren't programs. Sending is `sendBeacon` or `fetch` with `keepalive`, never awaited: tuning never waits on it, and failures are silent. `TvApi.reportWatch` carries it, so tests opt in.
- **Station tab.** A third /admin tab, Station (`admin/StationPanel.tsx`), lists the station manager's recent runs from `GET /api/admin/station/runs`: "Week of Nov 8, 2026" and the status (Applied, Proposed, Dry run, No change, or Failed in `--rc-admin-error`), the summary, "4 h 28 min watched in 4 weeks", the model's own summary, the power hours ("Power hour 8 PM to 9 PM: The Simpsons") and each change ("The Simpsons, 8 PM part: x1 to x1.62 · finished every time"), and what was left out. The latest applied run, while it is still the head revision, has "Undo this week's changes" (`POST /admin/undo`, then the usual toast). A line on top says when it runs and whether changes apply on their own. Same cards, rows and tokens as the Media tab.
- **Tests.** Vitest: `lib/watch.test.ts` (heartbeats, program end, tune-away, surfing, bumpers and filler, the runtime cap, beacon on power-off, silent failures, the `postWatch` transport), `App.test.tsx` (a channel change reports the program left), `admin/AdminApp.test.tsx` (Station tab runs, labels, undo, empty state). Playwright `e2e/admin.spec.ts`: the Station tab at 390 px, and `POST /api/watch` accepts a report and refuses a bad one.

## Implemented (Phase 8: the Report tab)

- **Report tab.** A fourth /admin tab, Report (`admin/ReportPanel.tsx`, labels in `admin/report.ts`), headed "Sunday report", reads `GET /api/admin/reports`. The latest report is one card: "Week of Nov 8, 2026" with "All good", or "Needs a look" in `--rc-admin-error` when the backups aren't; what waits for Brandon ("Waits for you: …"); Viewing ("54 min watched (last week 15 min)" and the top shows with their channel and time); Station manager (the run's summary, or "Didn't run this week."); On the air ("6,301 episodes (+412) of 140 shows, 79 movies" and "Nothing yet for N carried titles: …"); Conversion ("3,028 files left, about 51 h; 3 failed"); Backups ("Last night's backup is on the NAS. Restore test passed", or why not) with up to 10 of the restore test's schedule warnings. Older reports follow as one line each: the week and its headline. Before the first report: "The first report comes Sundays at 8 AM." Section labels are caption-size, muted, upper case; otherwise the Station tab's cards and tokens.
- **Tests.** Vitest `admin/AdminApp.test.tsx`: the latest report's sections and labels, older headlines, focus on the heading, and the empty state.

## Implemented (Nov 10 2026: All Years)

- **Year picker.** Under the 1990–2010 grid, an "All Years" cell has the fourth row to itself (▼ from the last row reaches it, ▲ goes back up). Its panel shows today's real date, "All Years: every channel on, airing every season of every show, whenever it first aired.", and the dial with every channel on. Picking it sends year 0 (`api.md`).
- **Labels.** The banner's button reads "All Years" instead of "Year 1998"; the guide's badge and the power-on screen read "All Years". `src/lib/years.ts` holds the range, `ALL_YEARS` and `yearLabel`; the stored year is read by `parseYear`, so a missing or bad value falls back to 1998 and a stored 0 restores All Years.
- **Tests.** Vitest: `lib/years.test.ts`, `screens/YearPicker.test.tsx` (picking All Years, reaching it with the arrows).
