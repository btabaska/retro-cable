# tvOS engineering handoff

Owner: engineer. Product spec: `docs/specs/tvos.md` (TV-1 ... TV-44). Design spec:
`docs/design/tvos.md` (D-1 ... D-14). Branch `m9/tvos`, cut from `m7/descriptions` at `c8e0e3d`
(what the homelab runs). No server change so far: the app uses the API as it is.

## Status

Milestones 1 (foundation) and 2 (Live TV) are done. The guide grid and the year picker are next
(see "What is left").

| Milestone | Scope | State |
| --- | --- | --- |
| 1 Foundation | Toolchain, XcodeGen project, RetroCableKit (models, client, contract test, tokens, layout, logic ports), power on, no signal, make targets | Done |
| 2 Live TV | One AVPlayer + AVPlayerLayer, tune queue and advance, banner and clock, channel up/down, last channel, rapid flipping, media failure retune, bumper slate, filler card, demo slate, duration-mismatch log, tune latency log, persisted year and channel, watch reporting wired to playback, background and foreground, simulated midnight, screensaver, Siri Remote mapping, simulator UI tests | Done |
| 3 Guide | Guide model (window, focus, scroll), grid, details panel, guide fetch and retry. The PiP (same player) and the guide screen's frame already exist | Next |
| 4 Year picker | Year grid focus, lineup cache with 120 ms delay and 1-5 s retry, dial in two columns, select and retune (`AppModel.changeYear` exists and is tested), picture hidden and silent while it shows | |
| 5 Long runs | Guide reopening after 30 simulated minutes in the background, full XCUITest flow (TV-42), hands-on checks | |

## How to build and test

All on a Mac with Xcode at `/Applications/Xcode.app` (the Makefile sets `DEVELOPER_DIR`).

| Command | What it does | Needs |
| --- | --- | --- |
| `make test-tvos` | `swift test` of `tvos/RetroCableKit` on macOS (162 tests, under 1 s) | Xcode or the Swift toolchain |
| `make tvos` | `xcodegen generate`, then `xcodebuild build-for-testing` for the tvOS Simulator with `CODE_SIGNING_ALLOWED=NO`: the app plus both test bundles | XcodeGen 2.46, Xcode with the tvOS SDK |
| `make test-tvos-ui` | `xcodebuild test` in the "RC Apple TV" simulator: `RetroCableTests` (fonts load) and `RetroCableUITests` (power on, Live TV remote, guide PiP, background). Saves screenshots to `tvos/build/screens/`. About 3 minutes | The simulator below; the homelab server reachable |
| `RC_FLIPS=1 make test-tvos-ui` | Also runs the opt-in 20-flip test for TV-40; read the times from the log (below) | as above |
| `RC_SERVER_URL=http://127.0.0.1:8080 make test-tvos-ui` | UI tests against the Docker test stack instead of the homelab | `make stack-up` |
| `make lint`, `make test` | Unchanged, Linux-only, no tvOS in them | as before |

The tvOS targets are outside `make lint` and `make test`, so Linux CI never needs Xcode.

### Toolchain on this Mac (verified Nov 11-12 2026)

- Xcode 27.2 (27B52), Swift 6.4.2, tvOS 27.2 and tvOS Simulator 27.2 SDKs and the tvOS 27.2
  simulator runtime, XcodeGen 2.46.0. No signing identity and no paired Apple TV yet.
- The simulator is **"RC Apple TV"** (Apple TV 4K (2nd generation) (at 1080p), tvOS 27.2, so
  screenshots are 1920x1080 points one to one). If it is missing: `xcrun simctl create "RC Apple TV"
  com.apple.CoreSimulator.SimDeviceType.Apple-TV-4K-2nd-generation-1080p
  com.apple.CoreSimulator.SimRuntime.tvOS-27-2`. Shut it down with `xcrun simctl shutdown "RC Apple TV"`.
- Simulator builds need no signing (`CODE_SIGNING_ALLOWED=NO` everywhere).
- Run the app by hand: `make tvos`, `xcrun simctl boot "RC Apple TV"`, `open -a Simulator`,
  `xcrun simctl install "RC Apple TV" tvos/build/Build/Products/Debug-appletvsimulator/RetroCable.app`,
  `xcrun simctl launch "RC Apple TV" us.tabaska.retrocable -RCNoWatch`. The Simulator app's
  remote (Window > Show Apple TV Remote) or the keyboard arrows, Return, Escape and Space drive it.
- Tune times and length mismatches are in the log: `xcrun simctl spawn "RC Apple TV" log show
  --last 10m --info --predicate 'subsystem == "us.tabaska.retrocable"'` (on the device: Xcode's
  console, or Console.app filtered to the subsystem). Lines read `tune ktvu 850 ms` and
  `The Simpsons S5E12: file runs 1381.0 s but its slot is 1376 s (+5.0 s)`; never a URL.

### Sideloading (Brandon)

1. `make tvos` once (it generates `tvos/RetroCable.xcodeproj`), then open that project in Xcode.
2. Target RetroCable > Signing & Capabilities: tick "Automatically manage signing" and pick your
   Personal Team. The bundle id is `us.tabaska.retrocable`; `DEVELOPMENT_TEAM` is left empty in
   `project.yml` on purpose, so regenerating the project clears the team: pick it again after
   each `make tvos`, or set it once in Xcode and avoid regenerating.
3. Pair the Apple TV (Settings > Remotes and Devices > Remote App and Devices on the TV; Xcode >
   Window > Devices and Simulators), choose it as the run destination and press Run.
4. A free Apple ID's provisioning lasts 7 days: run from Xcode again to renew.

## Layout

| Path | What |
| --- | --- |
| `tvos/project.yml` | XcodeGen spec: app `RetroCable` (tvOS 18.0), `RetroCableTests` (unit, hosted), `RetroCableUITests` |
| `tvos/RetroCableKit/` | Swift package, platforms tvOS 18 + macOS 14, Swift 6 language mode (strict concurrency). All logic |
| `tvos/RetroCable/App/` | `RetroCableApp` (entry point, audio session, `-RCNoWatch`) and `RootView` (screens, the one player moving between full screen and the PiP, TV-on reveal, scene phase, screensaver) |
| `tvos/RetroCable/Playback/` | `AVPlaybackEngine`: the app's one `AVPlayer`, implementing the kit's `PlaybackEngine` |
| `tvos/RetroCable/Views/` | Thin SwiftUI views: `PowerOnView`, `NoSignalView`, `LiveTVView` (banner, clock, remote), `InfoBannerView` (banner, badges, diamond, clock box, hints), `PlayerView` (AVPlayerLayer, bumper slate, filler card, demo slate), `GuideView` (header, PiP frame, footer; grid in milestone 3), `Theme` (palette and fonts) |
| `tvos/RetroCable/Resources/` | Fonts with their OFL licenses, `Assets.xcassets` (launch color) |
| Generated, gitignored | `tvos/RetroCable.xcodeproj/`, `tvos/RetroCable/Info.plist`, `tvos/build/`, `tvos/RetroCableKit/.build/` |

### RetroCableKit

| File | What | Tests |
| --- | --- | --- |
| `Timestamp.swift` | Exact ISO 8601 parsing (offsets, microseconds), calendar days | `TimestampTests` |
| `Models.swift` | `ChannelInfo`, `Lineup`, `GuideSlot`, `TunedSlot`, `Tune`, `GuideRow`, `Guide`, `ServerVersion`, `Health`, `WatchReport`; `SignedPath` hides media paths from every print | `ContractTests`, `ModelTests` |
| `APIClient.swift` | `/api` client over an injectable `HTTPTransport`; `APIError` carries no URL; `ServerConfig` | `APIClientTests` |
| `DesignTokens.swift` | `design/tokens.json` as typed constants; `Color(rc:)` | `DesignTokensTests` |
| `TVLayout.swift` | tvOS-only geometry from the design spec | `TVLayoutTests` |
| `FontFace.swift` | The five bundled faces, file names and pinned hashes | `FontFaceTests` |
| `Years.swift` | Ports of `years.ts`, `channels.ts`, `focus.ts`, plus the dial and the starting channel | `PortedLogicTests` |
| `Program.swift`, `TimeFormat.swift`, `GuideLayout.swift` | Ports of `program.ts`, `time.ts`, `guide.ts`; `SimulatedTime`, `MonotonicClock` | `PortedLogicTests` |
| `WatchReporter.swift` | Port of `watch.ts` with `device: "appletv"` and fire-and-forget posting | `WatchReporterTests` |
| `Settings.swift` | `rc.year`, `rc.channel` in UserDefaults | `SettingsTests` |
| `AppModel.swift` | Screens, power on, server line, lineup, no signal; the Live TV remote (`move`, `pressSelect`, `pressPlayPause`, `pressBack`, `backLeavesApp`), channel up/down and last channel, the 4 s banner, `changeYear`, watch reporting from the tuner's slot, heartbeat, background and foreground, simulated midnight | `AppModelTests`, `LiveModelTests` |
| `Tuner.swift` | The /tune queue and the player it drives: live start position, advance at slot end with the 5 s overrun, refill at the second-to-last slot, one media retune then filler, 5 s /tune retry, stale-answer dropping, latency and length-mismatch logs | `TunerTests` |
| `Playback.swift` | `PlaybackEngine` protocol and `PlaybackEvent`, `Picture`, `BumperSlate`, `DemoSlate`, `TuneLatencyLog` | `TunerTests`, `BannerTests` |
| `LiveText.swift` | `BannerContent` (every banner field), `ClockText`, remote hints, the length-mismatch line | `BannerTests` |
| `Scheduling.swift` | `TimerScheduler` (tasks in the app, a manual clock in tests) and `TimerHandle` | through the above |
| (tests only) | XcodeGen spec checks: bundle id, team, server URL, no ATS keys, gitignore | `ProjectSpecTests` |

The contract test builds sample objects from `server/openapi.json` itself: a full sample must
round-trip through each model with exactly the schema's keys (so a field the server adds, drops or
renames fails), and a required-fields-only sample with nulls must decode. It also checks the paths,
parameters and response schemas the client calls. It already caught one thing: `WatchReport.offset`
is optional in the contract, so the model defaults it to 0.

Retries never sleep in tests: `AppModel` takes a `Sleeper`, and the tests record the requested
delays (`[5, 5, 5]`) while a stub transport brings the server back.

Live TV tests run on a `ManualClock` (the monotonic clock and the timer scheduler in one: a test
advances it and due timers fire in order), a `FakeEngine` (records load/start/stop and sends
ready, first frame, failed and ended) and a `TuneServer` whose /tune answers the test hands out in
any order (`LiveFakes.swift`). So rapid flipping, slot ends, the 5 s overrun, 60 s heartbeats, the
4 s banner and simulated midnight are all tested without waiting.

### How Live TV works

- `AppModel` owns one `Tuner`, which owns no player: it drives the `PlaybackEngine` it is given
  (`AVPlaybackEngine` in the app, `FakeEngine` in tests).
- A tune (`Tuner.tune`) stops the old item at once (picture to black, sound off), bumps a serial,
  and asks /tune. Only the latest serial's answer is used; earlier ones are dropped. The answer's
  `now` becomes simulated time (`SimulatedTime` on the monotonic clock).
- A slot with media is loaded; when the item is ready the tuner computes simulated now - slot
  start, clamps it to 0.25 s before the file's end, and the engine seeks (1 s tolerance either
  side) and calls `playImmediately`. The first `timeControlStatus == .playing` after that is the
  first frame: the picture switches from black to video and the press-to-first-frame time is
  logged. A slot without media shows the bumper slate or the filler card at once.
- At slot end (simulated) the next slot starts; a file with 0-5 s left finishes first (its end
  event, or the remaining time + 0.25 s). When the slot playing becomes the second to last fetched,
  /tune is asked again; if its answer still has the playing slot, the queue is swapped around it
  without a reload.
- A failed file retunes once for a fresh URL (the answer restarts at the live position); a second
  failure (or a failed retune) shows the filler card with the title until the slot ends.
- `AppModel` hands `WatchReporter` the playing slot whenever it changes (live or guide), sends a
  heartbeat every 60 s, and ends with `power_off` on background; the reporter handles
  `program_end`, `tune_away` and the 10 s minimum.

## Acceptance criteria covered so far

| Criterion | State |
| --- | --- |
| TV-1 | Done: `make tvos`, `make test-tvos`, `make lint`, `make test` all pass; `.xcodeproj` gitignored |
| TV-2 | Ready for hands-on: bundle id, tvOS 18.0, empty team, `RCServerURL`, no ATS keys (tested); the simulator reaches `https://tv.home.tabaska.us` with no ATS exception |
| TV-3 | Done (unit; UI test passes in the simulator) |
| TV-4 | Done (unit; UI test: Select turns on to Live TV on 02 with the banner) |
| TV-5 | Done in the model (5 s retries, automatic switch, unit-tested); hands-on left |
| TV-6 | Done in the simulator: AVPlayerLayer aspect-fit on black, 4:3 pillarboxed (screenshot), no system UI for any press in the UI tests. Hands-on left |
| TV-7 | Offset math unit-tested; in the simulator the picture matched the schedule. Hands-on parity with the browser left |
| TV-8 | Unit: advance at slot end, 5 s overrun, refill without restart, 12 slots back to back. Hands-on evening left |
| TV-9, TV-10, TV-11, TV-12 | Done (unit); bumper slate, filler card and demo slate drawn in `PlayerView` |
| TV-13, TV-14, TV-15, TV-17 | Done (unit and UI tests in the simulator) |
| TV-16 | Done: holding Select (1 s in the UI test) doesn't open the guide; a click does |
| TV-18, TV-19, TV-20, TV-21 | Done (unit; banner and clock screenshots) |
| TV-27 | The PiP is the same player moved to the PiP frame (UI test and screenshot: it keeps playing). The guide grid is milestone 3 |
| TV-28 | Back returns to Live TV with no retune (unit, UI); Select and Play/Pause in the guide come with milestone 3 |
| TV-32, TV-33 | Done (unit): year and channel saved, fallback channel not saved, stored channel returns in a year where it is on the dial, last channel not saved |
| TV-34, TV-35, TV-36 | Done (unit): sessions from the playing slot, live and PiP, 60 s heartbeat, program_end, tune_away, power_off, under 10 s never sent, fire and forget. Hands-on: rows in the Sunday report |
| TV-37 | Background and foreground done (unit, UI test). Reopening an old guide is milestone 3 |
| TV-38 | Done (unit): lineup refetched 1 s after simulated midnight in the server's zone |
| TV-39 | `isIdleTimerDisabled` while Live TV or the PiP plays. Hands-on left |
| TV-40 | Tunes start on the press (unit); latency logged per tune (unit, log). Simulator, 20 spaced flips against the homelab: median 520 ms, revisits ~330 ms, slowest 1.95 s and 1.99 s (cold files with software decode). Device p95 is hands-on |
| TV-41 | Signed paths never print (tested); tuner logs carry no path, `exp`, `sig` or host (tested); fixtures carry none (tested) |
| TV-44, D-1, D-3, D-13 | Done: tokens test, five fonts with hashes and UIAppFonts, new tokens in `tokens.json` |
| D-2 | View code uses `DesignTokens` / `TVLayout`; the SMPTE bar colors are the one literal set |
| D-5, D-6 | Done in the simulator (screenshots): pillarbox, cut to black then picture, banner geometry and motion |
| D-11 | Power on and no signal use the radial gradient; launch screen is the `header` token (tested) |
| D-12 | Banner hints: "▲▼ Channel  SELECT Guide  ▶❚❚ Last channel" (tested) |

## Decisions

Open questions the PM and designer raised, decided for this build (Brandon can overrule):

- **Hold Select = like**: reserved and does nothing. A likes feature needs a server change (field
  or endpoint plus a station-manager input); not in this phase.
- **Year picker shortcut**: none for now; Play/Pause in the guide, as the spec says. A long press
  of Play/Pause on Live TV is easy to add later if two presses feel slow.
- **Back on Live TV**: as the spec says (shows the banner when hidden, leaves when it shows).
- **Power on every cold launch**: kept, as in the browser. It also shows the app version, so
  Brandon can tell which sideload is installed.
- **Local network prompt**: `NSLocalNetworkUsageDescription` is in Info.plist now ("Retro Cable
  plays TV from the cable box server on your home network."). If tvOS never prompts, it is unused.
- **Caption size (16 pt), title-safe insets (80/60 pt)**: as designed; Brandon judges from the
  couch. Both are one-line changes (`fontSize.caption`, `TVLayout.safeSide`).
- **Web switching its hex literals to the new tokens**: a later web PR, not this one.
- **App icon**: left as the default for sideloading; the build warns that there is no "App Icon &
  Top Shelf Image" set. Build the two-layer RC icon if the install refuses or once the paid account
  and TestFlight work start.
- **Server URL overrides**: `-RCServerURL <url>` launch argument or `RC_SERVER_URL` environment
  variable, for the XCUITests against the Docker test stack. Info.plist has no ATS keys; if the
  simulator refuses plain `http://127.0.0.1:8080`, add `NSAllowsLocalNetworking` to a test-only
  configuration, never to the build Brandon installs.
- **Fonts are assets, not dependencies**: Arimo (googlefonts/Arimo @ `22e482e`) and Archivo
  Narrow (Omnibus-Type/ArchivoNarrow @ `9793ec7`), SIL OFL 1.1, TTFs and `OFL-*.txt` in
  `tvos/RetroCable/Resources/Fonts/`. No third-party Swift packages.
- **Tests**: Swift Testing in the kit and the app unit tests; XCTest for XCUITest.

Milestone 2 decisions:

- **No number entry**: the Siri Remote has no digits (spec, design 4.4).
- **Remote input**: one focusable SwiftUI view per screen (`remoteControl` in `LiveTVView.swift`)
  with `.focusEffectDisabled()`: `onMoveCommand` (swipes and clicks), `onTapGesture` (Select),
  `onLongPressGesture(minimumDuration: 0.5)` that does nothing (hold Select), `onPlayPauseCommand`,
  and `onExitCommand`, which is passed `nil` while the banner shows so Back leaves the app.
  Verified with XCUIRemote in the simulator; a long press does not also fire the tap.
- **Seek tolerance and start**: the engine seeks with 1 s tolerance either side and starts with
  `playImmediately(atRate: 1)`. That cut cold tunes in the simulator noticeably and keeps the
  picture within TV-7's 2 s. If the device misses 1.5 s, the next levers are a shorter
  `preferredForwardBufferDuration` and pre-loading the next slot's item.
- **First frame** is the first `timeControlStatus == .playing` after the seek, which is what the
  latency log measures.
- **A file shorter than its slot** goes to black for the remainder, then the next slot starts on
  schedule (programs are never cut; this only covers a file that ends early).
- **`-RCNoWatch`** (or `RC_NO_WATCH=1`) drops watch reports. The UI tests and every simulator
  session in this work used it, so no test viewing reached the homelab's station manager.
- **The guide screen** exists as a frame (header with clock and year badge, PiP, footer) so the
  PiP and Back work now; Select in it returns to Live TV for now. Milestone 3 replaces the body.
- **UI tests read the homelab's 1998 dial** (02, 04, 05, 09, 20, 21, ... 95). If the lineup
  changes, update the expected numbers in `LiveTVTests`, or point them at the test stack.

## Screenshots (milestone 2)

`make test-tvos-ui` writes these to `tvos/build/screens/` (gitignored; they contain frames of real
media, so they never go in the repo). Taken Nov 11 2026 in the "RC Apple TV" simulator against
`https://tv.home.tabaska.us` (server `c8e0e3d`):

| File | Shows |
| --- | --- |
| `tv-power-on.png` | Power on: wordmark, place and year, gold button, server line, app version |
| `tv-live-banner.png` | Live TV right after turning on: 4:3 picture pillarboxed, clock box, banner with description, progress, next and hints |
| `tv-live.png` | Live TV after the banner left: picture only |
| `tv-guide-pip.png` | The guide frame with the live PiP (3 pt rule border) at x 1517, y 146 |

Compared with `docs/design/tvos/web-live-banner.png` and `web-power-on.png`, the banner, clock box
and power-on screen line up at the design's geometry; the banner grows upward when the
description takes two lines, as designed.

## What is left

- Milestone 3, the guide: the grid, focus and scrolling, details panel, /guide fetch with the
  5 s retry, Select tunes the focused row's channel (`AppModel.tune(to:)`), Play/Pause opens the
  year picker, and a guide older than 30 simulated minutes reopens at the current half hour after
  the background (TV-22 to TV-28, TV-37's last part).
- Milestone 4, the year picker (TV-29 to TV-31): the screen, its lineup cache and retry, and
  hiding and silencing the picture while it shows (`Tuner.stop()`, then `changeYear` or a retune
  on Back).
- Milestone 5: the full XCUITest flow (TV-42) and D-14 screenshots of the guide and year picker.
- Hands-on checks on the Apple TV: TV-2, TV-5, TV-6, TV-7, TV-8 (an evening), TV-27, TV-34 (rows in
  the Sunday report), TV-39, TV-40 (p95 from the log over 20 flips), TV-43.
