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
| Year picker | 1990–2010 grid, shows the simulated date and which channels are on the dial | Select = set year and retune |
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
- **Demo slate.** Demo feeds are generic color bars, so the player overlays the show, season, episode and title. During bumpers it shows the call sign and "Up next".
- **Live TV keys.** ▲ / ▼ (or Page Up / Down) change channel, wrapping and skipping channels not on the dial. Space or P goes to the last channel. Digits tune by number. Enter opens the guide, Y opens the year picker, and Esc, I or ◀ / ▶ show the banner. The info banner shows for 4 s on every tune, with the clock and simulated date.
- **Guide.** One request covers 12 hours from the current half hour. The grid shows 7 rows × 90 minutes. Cells are sized to real run times, and each bumper is folded into the cell before the next program. A cell that started before the window reads "from 7:52". Up and down keep the time anchor. Left and right move by cell and scroll the window by half hours. Enter tunes, Space opens the year picker, and Esc goes back. The details panel shows the focused cell, and the live picture plays as picture-in-picture.
- **Year picker.** A 7 × 3 grid of years. The panel shows the dial for the focused year, with hidden channels marked "Hidden until …". A failed lineup request is retried after 1 s, then 2 s and so on up to 5 s, so the dial never stays empty. Enter sets the year, retunes and returns to live TV. The year and channel are remembered in `localStorage`.
- **Tests.** Vitest covers guide layout, channel stepping, time formatting and the app flow on a fake API. The Playwright flow `e2e/tv.spec.ts` runs launch → banner → guide → tune → change year, and checks that the player is past the offset `/tune` returned. Playwright starts the API through `tools/e2e-api.sh` on the demo database.

## Implemented (Phase 7: Feel)

- **Guide cell kicker.** After the start time: `NEW` in the focus gold, bold; `MOVIE` for movies; the holiday name in `color.holiday`, bold and upper case (`8:00 · NEW · HALLOWEEN`). On a focused cell the kicker uses `onFocus`. The words are always text, never color alone.
- **Badges.** The info banner and the guide details show `NEW` (`.badge-new`) and the holiday (`.badge-holiday`: 2px holiday-color border and text, no fill). The details pane also shows a `MOVIE` outline badge. Bumpers and reruns never get NEW.
- **Movies.** The line under the title reads the premiere year and running time ("1998 · 1h 25m") instead of season and episode (`src/lib/program.ts`). Cells keep their true length.
- **Bumpers without media** show a station ID slate on `color.background`: number and call sign, channel name, and "Up next 8:27 Seinfeld". Bumpers with media play them.
- **Tests.** `program.test.ts` (kicker and labels), `InfoBanner.test.tsx` (badges, movie line) and `Player.test.tsx` (bumper slate).

## Implemented (Phase 10: /admin)

- **Route.** `/admin` (and anything under it) renders the phone admin (`src/admin/`) instead of the TV. Production Caddy already serves `index.html` for unknown paths.
- **Tokens.** `design/tokens.json` has an `admin` group, which becomes `--rc-admin-*`. `$`-prefixed keys inside a group are skipped.
- **Shell.** A 64 px ink header, a scrolling body on `admin.bg` with a 16 px gutter, and a 64 px tab bar. In M1 the tab bar shows only Channels; Holidays, Manager and Build are hidden.
- **Channel list.** 48 px rows: number, name, and meta ("Network · 12 shows", "Library · 2 shows", "from 2001 · …"). Custom channels have a dashed border and "Custom". A row opens the editor; YAML channels open read-only. Loading shows 6 skeleton rows, and an error shows a card with Retry. "+ New channel" is gold.
- **Editor.**
  - While an existing channel loads, the header shows its name from the list and the body shows skeleton fields and rows: no values, no shows message, no footer. A failed load shows a card with Retry. The title never falls back to a placeholder like "Channel".
  - Fields: number (`inputmode=numeric`, with the next free number as placeholder), call sign (upper case, max 6), name, type, "On the dial from" year, and a Library switch ("Available in every year").
  - Shows list: tap a row to set from/until years or "Remove from channel".
  - Sticky footer: "Preview week" and gold Save. Save is enabled once the draft changed and has a number, a name and a show, and reads "Checking…" while the server checks.
  - Failed checks show a summary card that takes focus and links to each field, plus inline messages tied to the field with `aria-invalid` and `aria-describedby`. Warnings show in a `warn` card.
  - Delete (custom channels) and leaving with unsaved changes both ask first.
- **Show picker.** A full-height sheet with an autofocused search. Results are grouped "Not on any channel", then "On other channels" ("on 27 Cartoon Network"). Shows already on the channel show ✓ and are disabled. Empty states: "No shows match '…'" and "Your library is empty. Run an import first."
- **Week preview.** A sheet with a year select and Sun–Sat day chips (today's date selected). It lists the day's programs with time and title, folds bumpers away, and shows NEW and holiday chips in the TV colors at 11 px. Failed checks show in place of the list.
- **Undo toast.** After a save or delete, an ink toast appears above the tab bar with a gold Undo. It lasts 10 s, pauses while hovered or focused, and has `role=status`. Undo shows "Restored".
- **Sheets** trap focus, close on Esc, return focus to the opener, and skip the slide animation under reduced motion. Tap targets are at least 44 px. `:focus-visible` is a 3 px accent outline.
- **Fix.** Number entry on live TV never committed: the commit timer depended on `props` and restarted on every 250 ms clock render. It now depends only on the digits and the dial.
- **Tests.** Vitest: `admin/draft.test.ts`, `admin/AdminApp.test.tsx` (list, create with picker, preview, save, undo, inline errors, discard, retry, editor loading and retry), `screens/YearPicker.test.tsx` (dial, lineup retry), and `lib/route.test.ts`. Playwright `e2e/admin.spec.ts` runs at 390 px: create a library channel from two uncarried shows, preview it, save, tune it by number on the TV and find it in the guide, then undo.
