import CoreGraphics
import Foundation
import Testing
@testable import RetroCableKit

/// The guide's grid model (web/src/screens/Guide.tsx): the window, focus and scrolling (TV-22 to
/// TV-25), cell text (TV-23, D-8) and the details panel (TV-26). Made-up titles only.
@Suite struct GuideGridTests {
    static let zone = Make.zone
    /// 7:30 PM, Wednesday Nov 11 1998.
    static let base = Make.base

    static func at(_ minute: Double) -> Date { base.addingTimeInterval(minute * 60) }

    static func channel(_ id: String, _ number: Int, network: String? = nil) -> ChannelInfo {
        Make.channel(id, number: number, network: network)
    }

    /// Back-to-back programs of `minutes` each from `from`, for `count` programs.
    static func run(_ prefix: String, from: Double = 0, minutes: Double, count: Int) -> [GuideSlot] {
        (0..<count).map { i in
            Make.slot("\(prefix) \(i + 1)", at: from + Double(i) * minutes, seconds: Int(minutes * 60)).slot
        }
    }

    static func guide(_ rows: [(ChannelInfo, [GuideSlot])]) -> Guide {
        Guide(
            date: "1998-11-11", weekday: "Wednesday", timezone: zone, start: base,
            end: base.addingTimeInterval(13.5 * 3600),
            channels: rows.map { GuideRow(channel: $0.0, slots: $0.1) }
        )
    }

    /// Ten channels of half-hour shows across the whole 13.5 h, numbered 2, 4, ... 20.
    static func tenChannels() -> Guide {
        guide((1...10).map { n in (channel("c\(n)", n * 2), run("C\(n)", minutes: 30, count: 27)) })
    }

    static func loaded(_ guide: Guide, now minute: Double = 5, tuned: String? = "c1") -> GuideGrid {
        var grid = GuideGrid(now: at(minute))
        grid.load(guide, tuned: tuned)
        return grid
    }

    // MARK: - Window (TV-22)

    @Test func opensOnTheCurrentHalfHour() {
        let grid = GuideGrid(now: Self.at(17))
        #expect(grid.opened == Self.base)
        #expect(grid.windowStart == Self.base)
        #expect(grid.windowEnd == Self.at(90))
        #expect(grid.focus.anchor == Self.at(17))
        #expect(grid.rows.isEmpty)
        #expect(grid.focusedCell == nil)
        #expect(GuideGrid(now: Self.at(31)).opened == Self.at(30))
    }

    @Test func asksForThirteenAndAHalfHoursFromTheWindowStart() {
        let grid = GuideGrid(now: Self.at(17))
        #expect(grid.query(zone: Self.zone) == ZonedParts(date: "11-11", time: "19:30"))
        #expect(GuideGrid.fetchHours == 13.5)
        #expect(grid.rangeEnd == Self.at(13.5 * 60))
    }

    @Test func timeBarShowsTheDayAndThreeHalfHours() {
        let grid = GuideGrid(now: Self.at(17))
        let bar = grid.timeBar(zone: Self.zone)
        #expect(bar.day == "Wed 11/11")
        #expect(bar.labels == ["7:30 PM", "8:00 PM", "8:30 PM"])
    }

    // MARK: - Rows and focus (TV-23, TV-24)

    @Test func rowsAreByNumberAndFocusStartsOnTheTunedChannelNow() {
        let guide = Self.guide([
            (Self.channel("late", 31), Self.run("Late", minutes: 30, count: 27)),
            (Self.channel("kfix", 2), Self.run("Fix", minutes: 23, count: 36)),
            (Self.channel("movies", 40), Self.run("Movie", minutes: 90, count: 9)),
        ])
        let grid = Self.loaded(guide, now: 50, tuned: "late")
        #expect(grid.rows.map(\.channel.id) == ["kfix", "late", "movies"])
        #expect(grid.focus.row == 1)
        #expect(grid.focusedCell?.slot.title == "Late 2")
        // Now is 8:20, so the window opens at 8:00.
        #expect(grid.windowStart == Self.at(30))
        #expect(grid.windowCells(1).first?.slot.title == "Late 2")
        #expect(grid.isFocused(row: 1, grid.windowCells(1)[0]))
        #expect(!grid.isFocused(row: 1, grid.windowCells(1)[1]))
        #expect(!grid.isFocused(row: 0, grid.windowCells(0)[0]))
    }

    @Test func anUnknownTunedChannelFocusesTheFirstRow() {
        let grid = Self.loaded(Self.tenChannels(), tuned: "nope")
        #expect(grid.focus.row == 0)
        #expect(grid.tunedRow(nil) == nil)
        #expect(grid.tunedRow("c3") == 2)
    }

    @Test func upAndDownMoveOneRowKeepTheTimeAndStopAtTheEnds() {
        let guide = Self.guide([
            (Self.channel("a", 2), Self.run("A", minutes: 30, count: 27)),
            (Self.channel("b", 4), Self.run("B", from: -10, minutes: 22, count: 37)),
            (Self.channel("c", 6), Self.run("C", minutes: 60, count: 14)),
        ])
        var grid = Self.loaded(guide, now: 5, tuned: "a")
        grid.move(.right)
        #expect(grid.focusedCell?.slot.title == "A 2")
        #expect(grid.focus.anchor == Self.at(30))
        grid.move(.down)
        #expect(grid.focus.row == 1)
        // B runs 22-minute shows from 7:20: 7:20, 7:42, 8:04: the one at 8:00 is B 2.
        #expect(grid.focusedCell?.slot.title == "B 2")
        #expect(grid.focus.anchor == Self.at(30))
        grid.move(.down)
        #expect(grid.focusedCell?.slot.title == "C 1")
        grid.move(.down)
        #expect(grid.focus.row == 2)
        grid.move(.up)
        grid.move(.up)
        grid.move(.up)
        #expect(grid.focus.row == 0)
        #expect(grid.focusedCell?.slot.title == "A 2")
    }

    @Test func theFocusedRowStaysFourthFromTheTop() {
        var grid = Self.loaded(Self.tenChannels(), tuned: "c1")
        #expect(grid.firstRow == 0)
        #expect(grid.visibleRows == 0..<7)
        for _ in 0..<3 { grid.move(.down) }
        #expect(grid.firstRow == 0)
        grid.move(.down)
        #expect(grid.focus.row == 4)
        #expect(grid.firstRow == 1)
        grid.move(.down)
        #expect(grid.firstRow == 2)
        for _ in 0..<10 { grid.move(.down) }
        #expect(grid.focus.row == 9)
        #expect(grid.firstRow == 3)
        #expect(grid.visibleRows == 3..<10)
        let few = Self.loaded(Self.guide([(Self.channel("a", 2), Self.run("A", minutes: 30, count: 27))]))
        #expect(few.firstRow == 0)
        #expect(few.visibleRows == 0..<1)
    }

    // MARK: - Left and right (TV-25)

    @Test func rightMovesOneCellAndScrollsByHalfHours() {
        var grid = Self.loaded(Self.tenChannels(), now: 5)
        #expect(grid.focusedCell?.slot.title == "C1 1")
        grid.move(.right)
        grid.move(.right)
        #expect(grid.focusedCell?.slot.title == "C1 3")
        #expect(grid.windowStart == Self.base)
        grid.move(.right)
        // C1 4 starts at 9:00, the window's end: the window moves half an hour to show it.
        #expect(grid.focusedCell?.slot.title == "C1 4")
        #expect(grid.windowStart == Self.at(30))
        #expect(grid.focus.anchor == Self.at(90))
    }

    @Test func neverScrollsMoreThanTwelveHoursAhead() {
        var grid = Self.loaded(Self.tenChannels(), now: 5)
        for _ in 0..<60 { grid.move(.right) }
        #expect(grid.windowStart == Self.at(12 * 60))
        #expect(grid.focusedCell?.slot.title == "C1 27")
        #expect(grid.windowCells(0).last?.slot.title == "C1 27")
    }

    @Test func leftScrollsBackButNeverBeforeTheOpeningWindow() {
        var grid = Self.loaded(Self.tenChannels(), now: 5)
        for _ in 0..<5 { grid.move(.right) }
        #expect(grid.windowStart == Self.at(90))
        for _ in 0..<5 { grid.move(.left) }
        #expect(grid.focusedCell?.slot.title == "C1 1")
        #expect(grid.windowStart == Self.base)
        grid.move(.left)
        #expect(grid.focusedCell?.slot.title == "C1 1")
        #expect(grid.windowStart == Self.base)
    }

    @Test func leftAtTheEarliestCellDoesNothing() {
        // A show that began before the window: the left edge is the start of the guide.
        let guide = Self.guide([(Self.channel("a", 2), Self.run("A", from: -10, minutes: 30, count: 28))])
        var grid = Self.loaded(guide, now: 5, tuned: "a")
        #expect(grid.focusedCell?.slot.title == "A 1")
        let before = grid
        grid.move(.left)
        #expect(grid == before)
        grid.move(.right)
        #expect(grid.focus.anchor == Self.at(20))
        grid.move(.left)
        // The anchor never goes before the window, so up/down keep landing on cells in view.
        #expect(grid.focus.anchor == Self.base)
    }

    @Test func movesWithNoRowsDoNothing() {
        var grid = GuideGrid(now: Self.at(5))
        let before = grid
        for direction in [MoveDirection.up, .down, .left, .right] { grid.move(direction) }
        #expect(grid == before)
    }

    @Test func staleAfterThirtySimulatedMinutes() {
        let grid = GuideGrid(now: Self.at(5))
        #expect(!grid.isStale(now: Self.at(30)))
        #expect(grid.isStale(now: Self.at(30.1)))
    }

    // MARK: - Geometry (D-7, D-8)

    @Test func cellsSitAtTrueRunTimesOnTheCellArea() {
        let guide = Self.guide([(Self.channel("a", 2), [
            Make.slot("Short", at: 0, seconds: 22 * 60).slot,
            Make.slot("Bumper", at: 22, seconds: 60, kind: .bumper, media: nil).slot,
            Make.slot("Movie", at: 23, seconds: 97 * 60, kind: .movie).slot,
        ])])
        let grid = Self.loaded(guide, tuned: "a")
        let cells = grid.windowCells(0)
        #expect(cells.map(\.slot.title) == ["Short", "Movie"])
        let short = TVLayout.cellFrame(left: cells[0].left, width: cells[0].width)
        #expect(short.minX == TVLayout.Guide.cellsLeft)
        // 23 minutes (the bumper folded in) at 16.6 pt a minute.
        #expect(short.width == TVLayout.snap(23 * TVLayout.Guide.pointsPerMinute))
        let movie = TVLayout.cellFrame(left: cells[1].left, width: cells[1].width)
        #expect(movie.maxX == TVLayout.safe.maxX)
        #expect(TVLayout.Guide.pointsPerMinute == 1494.0 / 90)
    }

    @Test func cellTextFollowsTheWidthClasses() {
        let slot = Make.slot("Wide Show", at: 3, seconds: 20 * 60, isNew: true).slot
        let cells = GuideLayout.row([slot], from: Self.base, to: Self.at(90))
        let full = GuideCellContent.make(cells[0], focused: false, now: Self.at(5), zone: Self.zone)
        #expect(full.size == .full)
        #expect(full.time == "7:33")
        #expect(full.kicker == [KickerPart(text: "NEW", tone: .new)])
        #expect(full.title == "Wide Show")
        #expect(!full.past)

        let narrowSlot = Make.slot("Narrow Show", at: 0, seconds: 7 * 60).slot
        let narrow = GuideCellContent.make(
            GuideLayout.row([narrowSlot], from: Self.base, to: Self.at(90))[0], focused: false, now: Self.at(5),
            zone: Self.zone
        )
        #expect(narrow.size == .narrow)
        #expect(narrow.time == nil)
        #expect(narrow.kicker.isEmpty)
        #expect(narrow.title == "Narrow Show")

        let sliverSlot = Make.slot("Sliver Show", at: 88, seconds: 30 * 60).slot
        let sliver = GuideCellContent.make(
            GuideLayout.row([sliverSlot], from: Self.base, to: Self.at(90))[0], focused: false, now: Self.at(5),
            zone: Self.zone
        )
        #expect(sliver.size == .sliver)
        #expect(sliver.time == nil)
        #expect(sliver.title == nil)
        #expect(sliver.accessibilityLabel == "Sliver Show, 8:58 to 9:28")
    }

    @Test func clippedCellsReadFromTheirStart() {
        let slot = Make.slot("Early Show", at: -8, seconds: 30 * 60).slot
        let cell = GuideLayout.row([slot], from: Self.base, to: Self.at(90))[0]
        #expect(GuideCellContent.make(cell, focused: true, now: Self.at(5), zone: Self.zone).time == "from 7:22")
    }

    @Test func pastHolidayTransplantAndMovieCells() {
        let slots = [
            Make.slot("Old Show", at: -20, seconds: 22 * 60, holidayName: "Halloween").slot,
            Make.slot("Moved Show", at: 2, seconds: 22 * 60, isNew: true, transplant: true).slot,
            Make.slot("Fixture Film", at: 24, seconds: 90 * 60, kind: .movie).slot,
        ]
        let cells = GuideLayout.row(slots, from: Self.base, to: Self.at(90))
        let old = GuideCellContent.make(cells[0], focused: false, now: Self.at(5), zone: Self.zone)
        #expect(old.past)
        #expect(old.holiday)
        #expect(old.size == .sliver)
        let moved = GuideCellContent.make(cells[1], focused: true, now: Self.at(5), zone: Self.zone)
        #expect(moved.transplant)
        #expect(moved.focused)
        #expect(!moved.past)
        #expect(moved.accessibilityLabel == "Moved Show, 7:32 to 7:54, new, pretend premiere")
        let film = GuideCellContent.make(cells[2], focused: false, now: Self.at(5), zone: Self.zone)
        #expect(film.kicker == [KickerPart(text: "MOVIE", tone: .plain)])
        #expect(film.accessibilityLabel == "Fixture Film, 7:54 to 9:24, movie")
    }

    // MARK: - Details panel (TV-26)

    @Test func detailsForAnEpisode() {
        let slot = Make.slot(
            "The Fixtures", at: 3, seconds: 23 * 60, season: 5, episode: 12, episodeTitle: "Bart Gets Fixed",
            isNew: false, holidayName: "Thanksgiving", description: "A fixture goes wrong.", airDate: "1994-02-03"
        ).slot
        let cell = GuideLayout.row([slot], from: Self.base, to: Self.at(90))[0]
        let details = GuideDetails.make(channel: Self.channel("ktvu", 2, network: "FOX"), cell: cell, zone: Self.zone)
        #expect(details.line == "02 · KTVU FOX · 7:33 – 7:56 PM")
        #expect(details.title == "The Fixtures")
        #expect(details.episode == "Bart Gets Fixed")
        #expect(details.badges == [.outline("S5 · E12"), .holiday("THANKSGIVING")])
        #expect(details.aired == "Rerun · first aired Thu, Feb 3, 1994")
        #expect(details.synopsis == "A fixture goes wrong.")
    }

    @Test func detailsForANewTransplantAndAMovie() {
        let moved = Make.slot(
            "Moved Show", at: 0, seconds: 22 * 60, season: 1, episode: 1, isNew: true, transplant: true,
            airDate: "1998-11-11"
        ).slot
        let movedCell = GuideLayout.row([moved], from: Self.base, to: Self.at(90))[0]
        let a = GuideDetails.make(channel: Self.channel("kfix", 2), cell: movedCell, zone: Self.zone)
        #expect(a.badges == [.outline("S1 · E1"), .new, .pretendPremiere])
        #expect(a.aired == "New · first aired Wed, Nov 11, 1998 (pretend)")
        #expect(a.episode == nil)
        // No description: the episode line, else the title.
        #expect(a.synopsis == "S1 E1")

        let film = Make.slot("Fixture Film", at: 0, seconds: 85 * 60, kind: .movie, airDate: "1995-06-01").slot
        let filmCell = GuideLayout.row([film], from: Self.base, to: Self.at(90))[0]
        let b = GuideDetails.make(channel: Self.channel("fmov", 40), cell: filmCell, zone: Self.zone)
        #expect(b.badges == [.outline("MOVIE")])
        #expect(b.synopsis == "1995 · 1h 25m")
        #expect(b.line == "40 · FMOV · 7:30 – 8:55 PM")

        let bare = Make.slot("Bare Show", at: 0, seconds: 22 * 60).slot
        let bareCell = GuideLayout.row([bare], from: Self.base, to: Self.at(90))[0]
        let c = GuideDetails.make(channel: Self.channel("kfix", 2), cell: bareCell, zone: Self.zone)
        #expect(c.synopsis == "Bare Show")
        #expect(c.aired == "Rerun")
        #expect(c.badges.isEmpty)
    }

    @Test func detailsForFiller() {
        let filler = Make.slot("Off the air", at: 0, seconds: 30 * 60, kind: .filler, media: nil).slot
        let cell = GuideLayout.row([filler], from: Self.base, to: Self.at(90))[0]
        let details = GuideDetails.make(channel: Self.channel("kfix", 2), cell: cell, zone: Self.zone)
        #expect(details.aired == "No programming scheduled")
        #expect(details.synopsis == nil)
        #expect(details.badges.isEmpty)
        #expect(GuideDetails.loading == "Loading the guide…")
        #expect(GuideDetails.noSignal == "No signal")
    }

    @Test func footerHintsNameSiriRemoteControls() {
        #expect(GuideDetails.hints.map(\.key) == ["SELECT", "▶❚❚", "BACK"])
        #expect(GuideDetails.hints.map(\.action) == ["Watch", "Change year", "Live TV"])
    }
}
