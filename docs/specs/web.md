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
