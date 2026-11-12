# tvOS engineering handoff

Owner: engineer. Product spec: `docs/specs/tvos.md` (TV-1 ... TV-44). Design spec:
`docs/design/tvos.md` (D-1 ... D-14). Branch `m9/tvos`, cut from `m7/descriptions` at `c8e0e3d`
(what the homelab runs). No server change so far: the app uses the API as it is.

## Status

Milestone 1 (foundation) is done. Live TV, the banner, the guide, the year picker, watch
reporting and background handling are the next milestones (see "What is left").

| Milestone | Scope | State |
| --- | --- | --- |
| 1 Foundation | Toolchain, XcodeGen project, RetroCableKit (models, client, contract test, tokens, layout, logic ports), power on, no signal, make targets | Done |
| 2 Live TV | One AVPlayer + AVPlayerLayer, tune queue and advance, banner and clock, channel up/down, last channel, rapid flipping, media failure retune, bumper slate, filler card, demo slate, duration-mismatch log, tune latency log | Next |
| 3 Guide | Guide model (window, focus, scroll), grid, details panel, PiP using the same player, guide fetch and retry | |
| 4 Year picker | Year grid focus, lineup cache with 120 ms delay and 1-5 s retry, dial in two columns, select and retune | |
| 5 Long runs | Watch reporting wired to playback, background and foreground, simulated midnight, screensaver, XCUITest flow, accessibility identifiers | |

## How to build and test

All on a Mac with Xcode at `/Applications/Xcode.app` (the Makefile sets `DEVELOPER_DIR`).

| Command | What it does | Needs |
| --- | --- | --- |
| `make test-tvos` | `swift test` of `tvos/RetroCableKit` on macOS (99 tests today) | Xcode or the Swift toolchain |
| `make tvos` | `xcodegen generate`, then `xcodebuild build-for-testing` for the tvOS Simulator with `CODE_SIGNING_ALLOWED=NO`: the app plus both test bundles | XcodeGen 2.46, Xcode with the tvOS SDK |
| `make lint`, `make test` | Unchanged, Linux-only, no tvOS in them | as before |

Both tvOS targets are outside `make lint` and `make test`, so Linux CI never needs Xcode.

### Toolchain on this Mac (verified Nov 11 2026)

- Xcode 27.2 (27B52), Swift 6.4.2, tvOS 27.2 and tvOS Simulator 27.2 SDKs, XcodeGen 2.46.0.
- Works: `swift build` and `swift test` of a package on macOS, with both Swift Testing and XCTest;
  `xcodegen generate`; `xcodebuild build` and `build-for-testing` with `-sdk appletvsimulator
  -destination 'generic/platform=tvOS Simulator' CODE_SIGNING_ALLOWED=NO` (about 15 s clean).
- Not available until Brandon finishes Xcode's first-launch setup: a simulator runtime and a
  signing identity. Never run `xcrun simctl` or `xcodebuild test` before that; `simctl` hangs.
  So the app-target tests (`RetroCableTests`, `RetroCableUITests`) compile today but have not run.

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
| `tvos/RetroCable/App/` | `RetroCableApp` (entry point) and `RootView` (switches screens) |
| `tvos/RetroCable/Views/` | Thin SwiftUI views: `PowerOnView`, `NoSignalView`, `LiveTVView` (placeholder), `Theme` (palette and fonts) |
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
| `AppModel.swift` | Screen state, power on, server line, lineup fetch, no-signal retry every 5 s | `AppModelTests` |
| (tests only) | XcodeGen spec checks: bundle id, team, server URL, no ATS keys, gitignore | `ProjectSpecTests` |

The contract test builds sample objects from `server/openapi.json` itself: a full sample must
round-trip through each model with exactly the schema's keys (so a field the server adds, drops or
renames fails), and a required-fields-only sample with nulls must decode. It also checks the paths,
parameters and response schemas the client calls. It already caught one thing: `WatchReport.offset`
is optional in the contract, so the model defaults it to 0.

Retries never sleep in tests: `AppModel` takes a `Sleeper`, and the tests record the requested
delays (`[5, 5, 5]`) while a stub transport brings the server back.

## Acceptance criteria covered so far

| Criterion | State |
| --- | --- |
| TV-1 | Done: `make tvos`, `make test-tvos`, `make lint`, `make test` all pass; `.xcodeproj` gitignored |
| TV-2 | Ready for hands-on: bundle id, tvOS 18.0, empty team, `RCServerURL`, no ATS keys (tested) |
| TV-3 | Done (text in unit tests; UI test written, not run) |
| TV-4 | Power on reaches Live TV on the stored channel (unit); the banner comes in milestone 2 |
| TV-5 | Done in the model (5 s retries, automatic switch, unit-tested) |
| TV-21 (formatting), TV-23 (layout port), TV-32, TV-33 (starting channel), TV-34/35/36 (reporter logic) | Logic ported and tested; wiring to playback comes later |
| TV-41 | Signed paths never print (tested); fixtures carry no `sig`, `exp` or token (tested) |
| TV-44, D-1, D-3, D-13 | Done: tokens test, five fonts with hashes and UIAppFonts, new tokens in `tokens.json` |
| D-11 | Power on and no signal use the radial gradient; launch screen is the `header` token (tested) |

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

## What is left

- Milestones 2 to 5 in the table above.
- Run `RetroCableTests` (fonts load) and `RetroCableUITests` once Xcode's simulator runtime is
  installed; extend the UI test to the full flow (TV-42).
- Hands-on checks on the Apple TV: TV-2, TV-6, TV-7, TV-27, TV-39, TV-40, TV-43; D-14 screenshots.
