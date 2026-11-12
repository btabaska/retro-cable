# Retro Cable on the Apple TV

Brandon's guide to building the Apple TV app and sideloading it onto the Apple TV 4K (2nd gen,
tvOS 18 or later) with a free Apple ID. What the app does is in `docs/specs/tvos.md`; how it is
built and tested, and what is left, is in `tvos/HANDOFF.md`.

The app plays the same channels as the browser app from `https://tv.home.tabaska.us`, so it works
only on the home network.

## 1. Xcode, once

Already done on this Mac (Nov 12 2026); repeat on any other Mac.

1. Install Xcode from the App Store (or developer.apple.com) into `/Applications/Xcode.app`.
2. Accept the license: open Xcode once and click Agree, or run
   `sudo xcodebuild -license accept`.
3. Install the tvOS platform: Xcode > Settings > Components, then Get next to tvOS (it brings the
   tvOS Simulator). Xcode also offers it in the first-launch window.
4. Install XcodeGen, which writes the Xcode project from `tvos/project.yml`:
   `brew install xcodegen`.
5. Sign in with your Apple ID: Xcode > Settings > Accounts, the + button, Apple Account. A free
   Apple ID shows up as "Brandon Tabaska (Personal Team)". No paid membership is needed for this.

Check it works: from the repo root, `make test-tvos` (the logic tests, about a second) and
`make tvos` (generates the project and builds the app for the simulator, unsigned).

## 2. Pair the Apple TV, once

1. Put the Mac and the Apple TV on the same network.
2. On the Apple TV: Settings > Remotes and Devices > Remote App and Devices. Leave this screen open;
   the Apple TV is now discoverable.
3. On the Mac: Xcode 27 lists Devices and Simulators in the Window menu only while a project is
   open, so run `make tvos` and `open tvos/RetroCable.xcodeproj` first. Then Window > Devices and
   Simulators > Devices: the Apple TV appears under Discovered. Select it and click Pair, then type
   the code the TV shows. Without Xcode's window, pair from Terminal (the prefix is needed while
   `xcode-select -p` points at the Command Line Tools):

   ```sh
   export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
   xcrun devicectl list devices                     # the Apple TV's name
   xcrun devicectl manage pair --device "<name>"    # then type the code the TV shows
   ```
4. Xcode then prepares the device (copies debug symbols for its tvOS version). The first time takes
   several minutes; wait until the device shows no warning.
5. If the Apple TV asks you to turn on Developer Mode (Settings > Privacy & Security > Developer
   Mode on recent tvOS versions), turn it on and let it restart.

## 3. Build and run on the Apple TV

1. From the repo root: `make tvos`. It generates `tvos/RetroCable.xcodeproj` (not committed) and
   checks that everything compiles.
2. Open the project: `open tvos/RetroCable.xcodeproj`.
3. Select the RetroCable target > Signing & Capabilities. Tick "Automatically manage signing" and
   choose Team: "Brandon Tabaska (Personal Team)". The bundle id is `us.tabaska.retrocable`; Xcode
   registers it to your Apple ID and makes a provisioning profile.
4. In the toolbar, choose the RetroCable scheme and your Apple TV as the run destination.
5. Product > Run (Cmd-R). Xcode installs the app and starts it on the TV.
6. The first launch of a free-account app may say the developer isn't trusted. On the Apple TV, find
   your Apple ID under Settings > General > Device Management (the menu name varies with the tvOS
   version), trust it, and launch Retro Cable again.

You should see the power-on screen: RETRO CABLE, "Mountain View, CA · 1998", the server line
("Server v0.0.0 · c8e0e3d") and the app version. Press the clickpad (SELECT) to turn the TV on.

The team id is not in the repo, and `make tvos` regenerates the project, which clears a team
picked in Xcode. To set it once for good:

1. After step 3, find your team id: Xcode > Settings > Accounts, select the Apple ID, then the
   Personal Team (a 10-character id such as `ABCDE12345`). It also shows in the target's Build
   Settings under Development Team once you have picked the team.
2. Create `tvos/Local.xcconfig` (gitignored) with that one line:

   ```sh
   echo 'DEVELOPMENT_TEAM = ABCDE12345' > tvos/Local.xcconfig
   ```

3. Run `make tvos` again. `tvos/Signing.xcconfig` includes `Local.xcconfig`, so every later
   regeneration keeps the team and the device build signs with no clicks.

Between code changes you can keep the project open and just press Run.

If tvOS asks to let Retro Cable find devices on the local network, allow it: the server is on the
home network.

## The Siri Remote

| Press | Live TV | Guide | Year picker |
| --- | --- | --- | --- |
| Swipe or click up/down | Channel up/down | Move one row | Move in the grid |
| Swipe or click left/right | Show the banner | Move one program, scroll by half hours | Move in the grid |
| Click (SELECT) | Open the guide | Watch the focused channel | Choose the year |
| Play/Pause | Last channel | Open the year picker | |
| Back | Show the banner; leave the app when it shows | Back to Live TV | Back to the guide |

Holding SELECT does nothing yet (it is kept for likes).

## The 7-day expiry

A free Apple ID's provisioning profile lasts 7 days. After that the app stops opening on the TV.
To renew: with the Apple TV on and paired, open the project in Xcode and press Run again. The saved
year and channel survive the reinstall. A free account also allows at most 3 sideloaded apps per
device and 10 new app ids a week, which this one app is well inside.

## Logs

Tune times and length mismatches go to the unified log under the subsystem `us.tabaska.retrocable`:
Xcode's console while running from Xcode, or Console.app with the Apple TV selected, filtered to
that subsystem. Lines look like `tune ktvu 850 ms`. Signed media URLs are never logged.

## Without the Apple TV: the simulator

`make test-tvos-ui` runs the unit and UI tests in the "RC Apple TV" simulator against the homelab
(about 5 minutes) and saves screenshots to `tvos/build/screens/`. To use the app by hand:

```sh
make tvos
xcrun simctl boot "RC Apple TV"
xcrun simctl install "RC Apple TV" tvos/build/Build/Products/Debug-appletvsimulator/RetroCable.app
xcrun simctl launch "RC Apple TV" us.tabaska.retrocable -RCNoWatch
xcrun simctl io "RC Apple TV" screenshot power-on.png
```

Xcode 27.2 has no Simulator app, so `open -a Simulator` fails. Its replacement for device and
simulator windows is DeviceHub (`open /Applications/Xcode.app/Contents/Applications/DeviceHub.app`);
whether it forwards a remote or the keyboard to an Apple TV simulator wasn't checked. `simctl` has
no command for remote presses, so past the power-on screen the UI tests (`make test-tvos-ui`) are
the way to drive the app. `-RCNoWatch` keeps simulator viewing out of the station manager's data.
Shut it down with `xcrun simctl shutdown "RC Apple TV"`.

## Later: TestFlight with the paid account

Once you join the Apple Developer Program ($99 a year), the app can come from TestFlight instead of
Xcode, last 90 days per build and update itself, as `docs/plan.md` planned for Phase 9:

1. Enroll at developer.apple.com, then sign in again in Xcode > Settings > Accounts; the paid team
   appears next to the Personal Team.
2. In App Store Connect, create the app record for `us.tabaska.retrocable` (platform tvOS) and an
   App Store Connect API key for automated uploads.
3. Put the paid team id in `tvos/Local.xcconfig` (or, for CI, in `tvos/Signing.xcconfig` through
   a build setting the runner passes). The app icon and a placeholder Top Shelf image are already
   in the asset catalog; a real Top Shelf image or extension is optional.
4. First upload by hand: Product > Archive, then Distribute App > TestFlight. Install the TestFlight
   app on the Apple TV and accept the internal tester invite.
5. Then the plan's automation: fastlane on the Mac runner signs and uploads on every merge to
   `main`, with a monthly rebuild so builds never pass the 90-day limit. That work, and the
   secrets it needs, belong in a separate `[ci]` PR.
