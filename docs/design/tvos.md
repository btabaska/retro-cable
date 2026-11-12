# tvOS design spec (Phase 9)

Owner: designer. Product behavior lives in `docs/specs/tvos.md` (PM). Where the two disagree on
*behavior*, the product spec wins; where they disagree on *look*, this file wins. The engineering
log is `tvos/HANDOFF.md`.

The Apple TV app should look like the browser app on a real TV: a flat, 2000s digital-cable look
(Motorola/Scientific-Atlanta era guide), navy fields, gold focus, condensed grid type. It is not a
modern tvOS app: no blur, no vibrancy, no parallax lift, no rounded posters, no shadows.

The browser app is the reference, not the old mockups. `docs/mockups/*.png` still show the
Favorites/Kids/Movies tabs, a Power Hour bar, a LIKED badge and a "HALLOWEEN" kicker in cells; the
web app dropped or changed those (see `docs/specs/web.md`), and so does tvOS.

## Reference screenshots

Captured from the live browser app (https://tv.home.tabaska.us, server `c8e0e3d`) at 1920x1080
with Playwright, every `/media/` request blocked so no video frame is in the repo, then halved to
960x540 and reduced to 128 colors to keep them small. Coordinates in this spec are full size
(multiply the screenshot's pixels by 2).

| File | Shows |
| --- | --- |
| `tvos/web-power-on.png` | Power-on screen: wordmark, place and year, gold button, server version |
| `tvos/web-live-banner.png` | Live TV right after a tune: clock box and info banner (video blocked, so black) |
| `tvos/web-guide.png` | Guide on open: focus on the tuned channel's current program, PiP top right |
| `tvos/web-guide-moved.png` | Guide after ▼ ▶ ▶: focus moved, details panel follows, 2-line synopsis |
| `tvos/web-year-picker.png` | Year picker: 1998 focused and current; the dial overflows (24 channels) |
| `tvos/web-no-signal.png` | No signal card (API blocked) |

The tvOS counterparts come from the "RC Apple TV" simulator at 1920x1080 points: `make
test-tvos-ui` writes `tv-power-on.png`, `tv-live-banner.png`, `tv-guide.png`, `tv-guide-moved.png`,
`tv-year-picker.png` and `tv-no-signal.png` (plus `tv-live.png`, `tv-guide-pip.png` and
`tv-year-picker-all-years.png`) to `tvos/build/screens/`. That folder is gitignored because the
frames hold real video; it is never committed.

Visible web defects that tvOS must not copy: the browser's white `:focus` ring around the focused
year cell, and the year picker's dial running off the bottom of the screen.

## 1. Canvas, points and title-safe area

- tvOS lays out a 1920x1080 point canvas on every Apple TV (it renders @2x on a 4K TV). One web
  stage pixel = one tvOS point, so every size in `design/tokens.json` and `web/src/styles.css`
  carries over unchanged unless this spec says otherwise.
- Title-safe area (Apple HIG, TVs that overscan): **60 pt top and bottom, 80 pt left and right**, so
  safe content lives in x 80...1840, y 60...1020.
- Rule: background fills, header and footer bars, the video and the radial gradients bleed to the
  screen edge (`.ignoresSafeArea()`). Text, badges, the PiP and every focusable thing stays inside
  the safe area.
- The web's 64 pt gutter (`space.gutter`) becomes 80 pt on tvOS (`TVLayout.safeSide`). Everything
  else in the guide is re-fitted to the safe area below.
- Snap every frame to 0.5 pt (the @2x pixel) so cell edges do not shimmer while the guide scrolls.
- Force dark: `.preferredColorScheme(.dark)`; set `.tint(DesignTokens.color.focus)`.

## 2. Tokens become Swift (DesignTokens.swift)

`design/tokens.json` stays the single source. tvOS reads it as Swift constants in the local package
(`tvos/RetroCableKit`, which builds on macOS so `swift test` runs today).

### Token additions (made in this change, additive, the web ignores them for now)

The web stylesheet had five literal colors with no token. They are now tokens so tvOS never
hard-codes a hex value:

| Token | Value | Web literal it replaces | Used for |
| --- | --- | --- | --- |
| `color.glow` | `#13306E` | `.power` gradient center | Power-on / no-signal radial gradient |
| `color.track` | `#2A3F75` | `.banner-progress` | Progress bar track |
| `color.divider` | `#1B2F5E` | `.dial-row` border | Dial row rules |
| `color.digits` | `#7CFF6B` | `.digits` | Number-entry digits (unused on tvOS, see 4.4) |
| `color.filler` | `#101010` | `.player-filler` | Filler card behind color bars |

The web's `#2e5aac` (blue button, active tab) equals `color.rule`; tvOS uses `color.rule`. A later
web change may switch its literals to these variables; that is not part of Phase 9.

### Shape of the Swift file

Hand-maintained `tvos/RetroCableKit/Sources/RetroCableKit/DesignTokens.swift`, one **struct per
token group with stored `let` properties**, so a test can enumerate it with `Mirror`:

```swift
public struct ColorTokens: Sendable {          // color.*, 0xRRGGBB
    public let background: UInt32 = 0x0B1E4A
    public let header: UInt32 = 0x06112E
    // ... one property per key in tokens.json "color", same name, same order
}
public struct FontSizeTokens: Sendable {       // fontSize.*, "16px" -> 16
    public let caption: CGFloat = 16
    // ...
}
public struct SpaceTokens, RadiusTokens, ScreenTokens   // same pattern, CGFloat
public struct FontTokens: Sendable {           // font.*: the first family of the CSS stack
    public let body = "Arimo"
    public let grid = "Archivo Narrow"
}
public enum DesignTokens {
    public static let color = ColorTokens()
    public static let fontSize = FontSizeTokens()
    public static let space = SpaceTokens()
    public static let radius = RadiusTokens()
    public static let screen = ScreenTokens()
    public static let font = FontTokens()
}
extension Color { public init(rc hex: UInt32) }   // sRGB, opacity 1
```

`DesignTokensTests` (in `RetroCableKitTests`, runs under `swift test`):

1. Finds `design/tokens.json` from `#filePath` (walk up to the repo root) and parses it with
   `JSONSerialization`.
2. For each group `color`, `fontSize`, `space`, `radius`, `screen`, `font`: the set of non-`$` keys
   equals the set of `Mirror(reflecting: DesignTokens.<group>).children` labels, both directions,
   and each value matches (`"#0B1E4A"` -> `0x0B1E4A`; `"16px"` -> `16`; `1920` -> `1920`; a font
   stack -> its first family with quotes stripped).
3. The groups it skips are exactly `["admin"]` (the browser-only /admin palette). A new group in
   `tokens.json` fails the test until someone decides whether tvOS needs it.

So adding or changing a token without updating Swift fails `swift test`, and so does a Swift
constant with no token. `Color(rc:)` gets a unit test for one known color.

### Layout constants (Swift only)

tvOS-only geometry (safe area, bar heights, guide grid numbers in this spec) goes in
`TVLayout.swift` in the same package, as `public enum TVLayout { static let ... }`. These are not
shared with the web, so they are not tokens. `TVLayoutTests` checks the arithmetic in section 5
(the grid fits between the details panel and the footer, the cell area is 1494 pt, the PiP stays in
the safe area).

## 3. Typography

### Fonts

The web bundles Arimo and Archivo Narrow from `@fontsource` (woff2 only). tvOS needs TTF, so the
app bundles the upstream static TTFs. Both are **SIL Open Font License 1.1**: free to bundle and
redistribute in an app, as long as the license text ships with them and the fonts are not sold on
their own.

| File (into `tvos/RetroCable/Resources/Fonts/`) | PostScript name | Source (pinned commit) | SHA-256 |
| --- | --- | --- | --- |
| `Arimo-Regular.ttf` | `Arimo-Regular` | `github.com/googlefonts/Arimo` @ `22e482e2ce0ebb2e6f88ba49d029d60f558d5d67`, `fonts/ttf/` | `41b22bc8f0b51f932825d37bc55b5eb6ba67dfe599a626e4aff2b43b624f9f8c` |
| `Arimo-Bold.ttf` | `Arimo-Bold` | same | `d7a8b187cf8444d4cfee102e8eae9e3043682fd5106d5d33ed677fe268a0e2ba` |
| `Arimo-Italic.ttf` | `Arimo-Italic` | same | `ffdf690e4357a392e4edcc3df0b3adb0302920f75e54f2e6eec355afbe7edcba` |
| `ArchivoNarrow-Regular.ttf` | `ArchivoNarrow-Regular` | `github.com/Omnibus-Type/ArchivoNarrow` @ `9793ec77b6682a26bc7a6ed523ca65cc3cb90aec`, `fonts/ttf/` | `8b2f285c60e450933c7deb3ad5acba00e5b34fb1b36f07490ab23d3f5e6df9e0` |
| `ArchivoNarrow-Bold.ttf` | `ArchivoNarrow-Bold` | same | `e1b016241fd2bf89796152d7875464ab0be7181160caa8a794b77cb94493fc1d` |

- Raw URLs: `https://raw.githubusercontent.com/<repo>/<commit>/fonts/ttf/<file>`. Ship each
  repo's `OFL.txt` next to them as `OFL-Arimo.txt` and `OFL-ArchivoNarrow.txt`. Name both in
  `tvos/HANDOFF.md` (they are assets, not code dependencies).
- Register them with `UIAppFonts` in the app's Info.plist (XcodeGen `info.properties`), all five
  file names. Fonts are not media; committing them is fine.
- Always `Font.custom(<PostScript name>, fixedSize:)`: sizes are fixed like the web stage, never
  scaled by Dynamic Type or Bold Text.
- Fallback if a font fails to register (log once, never crash): body -> `.system(size:, weight:)`
  (SF Pro); grid -> `.system(size:, weight:).width(.condensed)` (SF Pro Condensed). An app-target
  test asserts `UIFont(name: "ArchivoNarrow-Bold", size: 10) != nil` and the same for the other
  four.

### Type roles

`F.body` = Arimo, `F.grid` = Archivo Narrow. R = Regular, B = Bold. Line height is the font's own
(~1.15) unless given.

| Role | Font | Size (token) | Color | Notes |
| --- | --- | --- | --- | --- |
| Screen wordmark ("GUIDE", "SET YOUR YEAR") | grid B | 44 `heading` | `text` | tracking 3 |
| Power-on wordmark "RETRO CABLE" | grid B | 140 | `text` | tracking 12 |
| No-signal card "NO SIGNAL" | grid B | 120 | `text` | tracking 10 |
| Header clock time | body B | 36 `clock` | `text` | right aligned |
| Header clock date | body R | 22 `small` | `textMuted` | "Wednesday, Nov 11, 1998" |
| Year badge | body B | 36 `clock` | `onFocus` on `focus` | |
| Tab "All Channels" | body B | 22 `small` | `text` on `rule` | |
| Details line "02 · KTVU FOX · 7:33 – 7:56 PM" | body B | 26 `body` | `textMuted` | |
| Details title | body B | 60 `title` | `text` | 1 line, tail truncation |
| Details episode | body R | 30 `label` | `text` | omitted when empty |
| Badge text | body B | 22 `small` | per badge (5.5) | |
| Details aired "Rerun · first aired Thu, Feb 3, 1994" | body R | 22 `small` | `textMuted` | |
| Details synopsis | `Arimo-Italic` | 22 `small` | `textDim` | 2 lines, line spacing 1.3 (`lineSpacing(6.6)`) |
| Guide day "Wed 11/11", half-hour labels | grid B | 26 `body` | `text` | |
| Channel number in grid | grid B | 30 `label` | `text` | always 2 digits |
| Channel call sign in grid | grid R | 26 `body` | `textMuted` | 1 line |
| Cell time + kicker "7:33 · NEW · MOVIE" | grid R | 16 `caption` | `textMuted`; NEW `focus` B | |
| Cell title | grid R (B when focused) | 26 `body` | `text` | 1 line, tail truncation |
| Footer legends | body R, key word B | 22 `small` | `text`; right legend `textMuted` | |
| Banner channel number | grid B | 88 | `text` | line height 1.0 |
| Banner call sign | body B | 30 `label` | `textMuted` | 1 line |
| Banner title | body B | 50 | `text` | 1 line |
| Banner episode label | body R | 26 `body` | `textMuted` | tail truncation |
| Banner description | body R | 22 `small` | `textMuted` | 2 lines, line spacing 1.3 |
| Banner progress times, "Next 7:56 …" | body R ("Next 7:56" B) | 22 `small` | `textMuted` / `text` | |
| Banner remote hint | body R, key word B | 16 `caption` | `textDim` | |
| Year cell | grid B | 44 `heading` | `text` | |
| Year kicker / sub | body R | 26 `body` | `textMuted` | |
| Year date "Wednesday, November 11, 1998" | body B | 60 `title` | `text` | `minimumScaleFactor(0.8)` |
| Dial title "On the dial in 1998" | body B | 26 `body` | `text` | |
| Dial number | grid B | 26 `body` | `text` | |
| Dial name | body R | 22 `small` | `text` | |
| Dial note | body R | 16 `caption` | `textMuted` | |
| Bumper / filler / slate titles | grid B | see 4.3 | `text` | |

Number columns (channel numbers, clock digits, progress times) use `.monospacedDigit()` so they do
not jitter as the clock ticks.

## 4. Live TV

### 4.1 Video and the 4:3 pillarbox

- One `AVPlayer` for the whole app, shown through an `AVPlayerLayer` in a `UIViewRepresentable`,
  not `AVPlayerViewController` (that brings its own transport bar, swipe-to-scrub and focus, which
  fight the channel keys). The same player view moves between full screen and the guide's PiP.
- `videoGravity = .resizeAspect`: never crop, never stretch. A 4:3 file shows as 1440x1080 at
  x 240, with `color.black` bars at x 0...240 and 1680...1920 (the pillarbox). A 16:9 file fills
  the screen. The view behind the layer is `color.black`.
- While a channel loads (after a tune, before the first frame) the picture is `color.black`. No
  spinner, no snow: a digital box cuts to black, then to picture.

### 4.2 Info banner (every tune, 4 s)

Shown on every tune and the other triggers in `docs/specs/tvos.md` (◀ / ▶, Back while hidden,
turning on, a year change, returning from the background). It is a status display: **nothing in it takes
focus** (focusable buttons would steal ▲ / ▼ from channel changing).

Geometry (points, full screen):

| Element | Frame | Style |
| --- | --- | --- |
| Clock box | top 60, right edge 1824 (right 96); hugs content | `header` fill, radius 6 (`radius.button`), padding 12 x 24; time 36 B over date 22 R `textMuted`, right aligned |
| Banner | x 96...1824 (width 1728), bottom edge 1016 (bottom 64), min height 190, grows upward | `header` fill, 5 pt top rule in `focus`, padding 20 x 40, square corners |
| Channel column | width 160, centered | number 88 grid B, call sign + network 30 B `textMuted` ("KTVU FOX") |
| Main column | flexible, gap 48 from the channel column and from the chip column; rows 12 apart | rows below |
| Row 1 | title 50 B, then (gap 20, baseline aligned) episode label 26 `textMuted`, NEW badge, holiday badge, PRETEND PREMIERE badge | one line, overflow truncates the episode label first |
| Row 2 (only when the slot has a description, not bumpers) | description 22 `textMuted`, 2 lines max | |
| Row 3 progress | start time 22, track (flexible, height 10, radius 5, `track`), end time 22; gap 16 | fill `focus`, width = elapsed / runtime on simulated time |
| Row 4 | "Next 7:56" B + title; right-aligned hint 16 `textDim` | bumpers are skipped for Next |
| Chip column | 2 chips stacked, gap 12 | "Guide": `rule` fill, `text` 22 B; "Year 1998" (or "All Years"): `focus` fill, `onFocus` 22 B; padding 12 x 28, radius 6 |

- Hint text on tvOS: **"▲▼ Channel  SELECT Guide  ▶❚❚ Last channel"** (key words bold). The chips
  are not buttons; they read as the remote legend plus the current year.
- Bumper: title row shows the channel name, no episode or badges, no description.
- Before the slot is known: title "Tuning…", empty progress.

### 4.3 Bumper slate and filler card (in the player area)

Drawn by the app when a slot has no `media_url`, sized to the player's frame (full screen or PiP),
so use fractions of the player height `h`:

- Bumper slate: whole player area `background`; centered column, gap 0.02h: call sign
  ("02 KTVU") grid B 60 (`title`) at full screen, channel name 30 `text`, "Up next 8:00 The
  Simpsons" 30 `textMuted`. In the PiP scale all three by h / 1080.
- Filler: a 4:3 box centered in the player (`filler` fill). SMPTE bars across the top 63% of the
  box, seven equal columns: `#C0C0C0 #C0C000 #00C0C0 #00C000 #C000C0 #C00000 #0000C0` (fixed
  broadcast colors, deliberately not tokens). Slot title centered, grid B at 0.07h, bottom at
  0.12h from the box bottom, tracking 2.
- Tuning: the same filler card titled "Tuning…", shown when a tune (or the next slot's file) has
  no first frame after 3 s, or `/tune` failed. A normal tune stays a hard cut to black, then to
  picture (section 9); the card only covers a slow tune or an outage.

### 4.4 Number entry

The Siri Remote has no digits, so tvOS has no number entry and the green 120 pt digits never show.
`color.digits` exists for parity only.

## 5. Guide

Layout, top to bottom (all bars bleed edge to edge; content inside x 80...1840):

| Band | y range | Contents |
| --- | --- | --- |
| Header bar | 0...129, then a 3 pt `rule` line 129...132 | content vertically centered in y 60...129 |
| Details panel | 132...398 | details text left, PiP right |
| Grid | 398...964 | time bar (48) + 7 rows (68 each, 6 apart) |
| Footer bar | 970...1080 | content vertically centered in y 970...1020 |

Background `background` behind everything.

### 5.1 Header

- Left: "GUIDE" wordmark at x 80; 40 pt gap; tab "All Channels" (`rule` fill, padding 8 x 20,
  radius 6, 22 B). Not focusable.
- Right, ending at x 1840: clock (time 36 B over date 22 `textMuted`, right aligned), 40 pt gap,
  year badge (`focus` fill, 36 B `onFocus`, padding 8 x 24, radius 6): "1998" or "All Years".

### 5.2 Details panel (y 132...398)

- Text column x 80...1474 (40 pt clear of the PiP), top padding 14, 6 pt between lines:
  details line, title, episode (when present), badge row, synopsis. This totals ~248 pt with all
  five present; the episode line simply drops out when empty, and nothing below moves up into the
  grid (the panel is a fixed height).
- Badge row order: `S5 · E12` outline, NEW, MOVIE outline, holiday, PRETEND PREMIERE, then the aired
  text. Filler reads "No programming scheduled" and has no synopsis.
- PiP: outer frame x 1514...1840, y 143...389 (326 x 246) = 3 pt `rule` border + video 320 x 240
  (4:3) at x 1517, y 146. Video is `.resizeAspect` on `black`.
- While loading: title "Loading the guide…"; on failure "No signal".

### 5.3 Grid geometry

- Time bar y 398...446, `header` fill: day column x 80...340 ("Wed 11/11", padding-left 20), then
  three half-hour labels at x 346, 844, 1342 (each 498 wide), each with a 2 pt `rule` line on its
  left edge and 12 pt text inset.
- Rows: row k (k = 0...6) spans y 452 + 74k ... 520 + 74k. 7 rows visible; the focused row sits 4th
  (index 3) whenever the list allows (`firstRow = clamp(focusRow - 3, 0, count - 7)`), same as web.
- Channel column x 80...340, `channel` fill, padding 0 x 20, number (30 B grid) and call sign
  (26 R grid, `textMuted`) 16 apart. The tuned channel has a 6 pt `focus` bar inside its left edge.
- Cell area x 346...1840 = **1494 pt for 90 minutes = 16.6 pt per minute**. A cell's frame:
  `x = 346 + left * 1494`, `width = width * 1494` (fractions from the same `layoutRow` logic as the
  web, ported to Swift), rounded to 0.5 pt. The last 4 pt of each cell (`space.cellGap`) are left
  as `background`, so cells read as separate blocks; a cell's text never enters that gap.
- Cells are sized to true run times; each bumper folds into the cell before the next program.
  A cell that started before the window reads "from 7:25".
- Cell fill: `cell`; already ended (`end <= simulated now`): `cellPast`; focused: `focus`.
- Cell content (padding 6 x 12 `space.cellPad`, vertically centered, 2 lines):
  line 1 caption 16 `textMuted`: start time ("7:33", no AM/PM) + kicker (" · NEW" in `focus` B,
  " · MOVIE" in `textMuted`); line 2 title 26.
- Width classes (thresholds in points of the 1494 cell area, same numbers as web `CELLS_PX` logic):
  - **full** (>= 120 pt, about 7 min): both lines.
  - **narrow** (40...120 pt): title only, padding 8 left / 4 right, tail truncation ("T…"). A
    narrow cell whose text room (width less the 4 pt gap, the padding and any diamond) is under
    42 pt draws no title, since a capital and the ellipsis don't fit at 26 pt ("W…" is 40.3 pt)
    and SwiftUI would show a lone "…"; its accessibility label keeps the title. Below about 58 pt
    (3.5 min) that means no text.
  - **sliver** (< 40 pt, about 2.4 min): no text, padding 0; its accessibility label is the title.
- **Holiday**: a 3 pt `holidayMuted` bar inside the cell's left edge (`onFocus` when focused). The
  holiday name appears only in the details panel and banner badges, never as a cell kicker.
- **Transplant mark**: a 9 x 9 pt square with a 2 pt stroke, rotated 45°, `transplant` color
  (`onFocus` when focused), 3 pt before and 10 pt after, vertically centered on the title. The
  turned square is 12.7 pt across, so the mark's frame is 12.7 pt and those gaps are visible gaps. Draw it
  as a SwiftUI shape, never the "◇" glyph. Footer right shows the same mark + "Pretend premiere".
- **NEW**: in cells, the gold " · NEW" kicker word; in the details panel and banner, the NEW badge
  (5.5). Always text, never color alone.

### 5.4 Footer (y 970...1080, content in 970...1020)

Left, 36 pt apart: **SELECT** Watch · **▶❚❚** Change year · **BACK** Live TV. Right (ends x 1840):
transplant mark + "Pretend premiere" in `textMuted`.

### 5.5 Badges (details panel and banner)

Radius 4 (`radius.badge`), padding 2 x 10, 22 B:

| Badge | Fill | Border | Text |
| --- | --- | --- | --- |
| NEW | `focus` | none | `onFocus` |
| `S5 · E12`, MOVIE | none | 2 pt `textMuted` | `text` |
| Holiday ("HALLOWEEN", upper case) | none | 2 pt `holidayMuted` | `holidayMuted` |
| PRETEND PREMIERE | none | 2 pt `transplant` | `text`, transplant mark first |

## 6. Year picker

| Band | Frame | Contents |
| --- | --- | --- |
| Header bar | as the guide (0...132) | "SET YOUR YEAR" wordmark at x 80; right: legend **BACK** Guide (22, not a button) |
| Left column | x 80...984, from y 180 | kicker, date, sub, year grid |
| Dial panel | x 1040...1840 (800 wide, 56 pt gap), y 180...1020 | channel list |

- Left column (width 904): kicker "Today on your TV is" 26 `textMuted`; date 60 B 8 pt below
  (`minimumScaleFactor(0.8)` for "Wednesday, September 30, 1998"); sub 26 `textMuted` 12 pt below,
  **two lines reserved** so the grid does not jump when All Years' longer sentence wraps; grid
  40 pt below (about y 399).
- Year grid: 7 columns, 16 apart, cell width (904 - 96) / 7 = 115.4, height 96, radius 6, `cell`
  fill, 44 grid B. 1990...2010 fill three rows; **All Years** takes the whole fourth row. Grid
  height 4 x 96 + 3 x 16 = 432 (ends about y 831).
- Focused year: `focus` fill, `onFocus` text. The year now playing: 3 pt `textMuted` ring inset
  inside the cell (kept when it is also focused). No system focus ring.
- Dial panel: x 1040...1840 (800 wide), y 180...1020, `header` fill, radius 8, padding 32.
  Title "On the dial in 1998" 26 B, 12 pt below it the list.
  - The web's single 52 pt-row list fits 14 channels; the live dial has **24**, so tvOS uses **two
    columns** (gap 24, each 356 wide), filled top to bottom in channel-number order, 12 rows each.
  - Row: height 56, 1 pt `divider` rule at its bottom; number (26 grid B, 40 wide), 12 pt gap,
    then two lines: name 22 `text` (1 line, tail truncation) over the note 16 `textMuted`
    ("On air", "Always on (classics)", "Your playlist", "Hidden until 2001").
  - Not on the air in that year: number and name `textDim`.
  - More than 24 channels: rows shrink to fit (minimum 44); beyond that the last row reads
    "+N more".
  - While the lineup loads, the panel shows the title only; it never collapses.

## 7. Power-on and no signal

Both are full-bleed radial gradients: `RadialGradient(colors: [glow, header], center:
UnitPoint(x: 0.5, y: 0.4), startRadius: 0, endRadius: 811)` (the web's `circle at 50% 40%`, 70% of
the farthest-corner distance of 1158 pt). Content is a centered column, 32 apart.

- **Power-on** (the cold-launch screen, as in the browser and `docs/specs/tvos.md`): "RETRO
  CABLE" (140, tracking 12); "Mountain View, CA · 1998" (30 `textMuted`; "All Years" for year 0);
  the gold "Press SELECT to turn on the TV" (`focus` fill, `onFocus` 30 B, padding 20 x 48,
  radius 6, always shown focused, no lift, not a system button); the server line ("Tuning…",
  "Server v0.0.0 · c8e0e3d" or "No signal", 22 `textDim`); and under it, 8 pt below, the app's
  version "App 1.0 (12)" (16 `caption`, `textDim`). Select or Play/Pause plays the TV-on effect
  (section 9) into Live TV.
- **No signal**: "NO SIGNAL" card (120 grid B, tracking 10, 6 pt `text` border, padding 24 x 64)
  over "Can't reach the cable box server. Retrying…" (30 `textMuted`). No button, nothing
  focusable; the app retries in the background and cuts to Live TV when the server answers.
- **Launch screen** (Info.plist `UILaunchScreen`): solid `header` (#06112E) so the system launch
  flows into power-on with no white flash.

## 8. Focus: cable box, not tvOS

- The focused item is **a solid `focus` (gold) fill with `onFocus` text, bold in grid cells**. That
  is the whole effect: no scale, no lift, no parallax, no shadow, no glow, no rounded focus
  platter, no motion on the touch surface.
- Never use the system focus look: no `.buttonStyle(.card)`, no default `Button` styles on tvOS
  (even `.plain` lifts on focus), no `.hoverEffect`. Focusable custom views use
  `.focusable()` with `.focusEffectDisabled()` and draw their own state from `@FocusState` or
  from the model.
- Guide (recommended, engineer decides): the grid is **one focusable view** and the focused cell is
  model state (row + time anchor), as in `web/src/screens/Guide.tsx`. It reads `.onMoveCommand`,
  `.onPlayPauseCommand`, `.onExitCommand` and select. The system focus engine moves by geometry,
  which would break the cable-box rule that ▲ / ▼ keep the time anchor and ◀ / ▶ move by cell
  and scroll by half hours.
- Moves are instant: the gold block jumps cell to cell with no animation, and the window snaps by
  half hours. Holding a direction repeats at the system's repeat rate.
- Year picker: the same rule, 7-wide grid, All Years row below.
- Live TV takes no visible focus at all.

## 9. Motion

All motion is short and flat. With Reduce Motion on, slides become plain fades of the same length.
The 4 s banner timer runs on the real clock, never simulated time.

| What | Animation | Duration / curve |
| --- | --- | --- |
| Banner + clock box appear (any trigger) | fade in + slide up 24 pt | 0.20 s ease-out |
| Banner + clock box hold | static | **4.0 s** from the most recent trigger (each one restarts it) |
| Banner + clock box hide | fade out | 0.30 s ease-in |
| Banner contents on a channel change while visible | swap instantly, no crossfade | 0 |
| Picture on a channel change | hard cut to black, hard cut to the first frame | 0 |
| Progress fill | moves in steps with the 1 s clock tick, not animated | 0 |
| Live -> guide, guide -> live | guide fades in / out; the player frame animates between full screen and the PiP frame | 0.20 s ease-in-out (the web's `transition: 0.2s`) |
| Guide focus move, half-hour scroll | snap | 0 |
| Year picker open / close | fade | 0.15 s |
| Power-on -> Live TV ("TV on", on Select) | the picture opens from a 4 pt white horizontal line at mid-screen to full height, then the banner appears | 0.35 s ease-out (Reduce Motion: 0.2 s fade) |
| No signal -> Live TV | same "TV on" | 0.35 s |

## 10. Accessibility and polish

- Contrast (WCAG ratio, from the token values): `onFocus` on `focus` 9.7; `text` on `cell` 10.8;
  `textMuted` on `cell` 6.5 and on `cellPast` 7.5; `textDim` on `header` 7.4; `holidayMuted` on
  `cell` 5.0; `transplant` on `cell` 6.4; `focus` (NEW kicker) on `cell` 6.5. All pass 4.5:1.
- VoiceOver: a guide cell reads "The Simpsons, 7:33 to 7:56, new, pretend premiere"; slivers too.
  The banner is one element read on appearance.
- App icon (optional for sideloading; Top Shelf is out of scope): a two-layer tvOS icon, back layer
  the power-on gradient, front layer "RC" in Archivo Narrow Bold white with the gold 5 pt rule
  under it. 400 x 240 @1x / 800 x 480 @2x.

## Design acceptance points

- **D-1** `DesignTokens.swift` exposes every non-admin group of `design/tokens.json` (color,
  fontSize, space, radius, screen, font) as typed constants; `swift test` fails if a key or value
  differs in either direction, and if a new group appears without a decision.
- **D-2** No hex color, font size or spacing value from the token file is written as a literal in
  view code; views use `DesignTokens` / `TVLayout` (SMPTE bar colors are the one exception).
- **D-3** Arimo (Regular, Bold, Italic) and Archivo Narrow (Regular, Bold), OFL 1.1, the pinned
  upstream TTFs above, are bundled with their `OFL.txt` files, registered in Info.plist, used at
  fixed sizes; a test proves all five load, and a condensed system font is the fallback.
- **D-4** Backgrounds, bars, gradients and video bleed to the screen edges; all text, badges, the
  PiP and focusable content stay inside x 80...1840, y 60...1020.
- **D-5** 4:3 video is pillarboxed (aspect-fit on black) full screen and in the PiP, never cropped
  or stretched; a channel change cuts to black, then to picture.
- **D-6** The info banner matches 4.2: x 96...1824, bottom 1016, 5 pt gold top rule, channel /
  title / badges / description / progress / next, no focusable element; it shows on every trigger
  in the product spec, holds 4.0 s on the real clock, fades in over 0.2 s and out over 0.3 s.
- **D-7** The guide matches section 5: header, fixed-height details panel with a 320 x 240 PiP
  (3 pt rule border) at x 1517, y 146, and 7 rows x 90 minutes over a 1494 pt cell area at
  16.6 pt/minute, cells at true run times with bumpers folded in.
- **D-8** Guide cells follow the width classes (full >= 120 pt, narrow 40...120, sliver < 40 with
  an accessibility label), past cells use `cellPast`, holiday cells a 3 pt `holidayMuted` left bar,
  pretend premieres a drawn 9 pt diamond, NEW a gold kicker word, and the tuned channel a 6 pt gold
  bar.
- **D-9** Focus is a solid gold fill with navy text (bold in cells) and nothing else: no lift,
  scale, parallax, shadow or system ring anywhere in the app; guide and year-picker focus moves
  snap instantly.
- **D-10** The year picker shows the 7 x 3 year grid plus a full-width All Years row, the current
  year ringed, and the dial for the focused year in two columns that fit all 24 channels on screen
  with off-air channels dimmed.
- **D-11** Power-on and no-signal use the `glow` -> `header` radial gradient and the web's
  type; no-signal has nothing focusable; the launch screen is solid `header`.
- **D-12** Remote legends on screen name Siri Remote controls (SELECT, ▶❚❚, BACK, ▲▼), never
  keyboard keys (ENTER, SPACE, ESC, Y).
- **D-13** Five new color tokens (`glow`, `track`, `divider`, `digits`, `filler`) are in
  `design/tokens.json`; `make lint` and `make test` stay green.
- **D-14** Screenshots of the tvOS screens (from the simulator once Xcode setup is done, or from
  device) placed beside `docs/design/tvos/web-*.png` line up with them within a few points at the
  geometry given here, apart from the documented tvOS changes (safe-area insets, two-column dial,
  remote legends, app version line).
