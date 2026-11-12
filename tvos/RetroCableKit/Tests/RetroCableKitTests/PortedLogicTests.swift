import Foundation
import Testing
@testable import RetroCableKit

// Ports of web/src/lib/*.test.ts. Each suite names the file it ports.

/// web/src/lib/years.test.ts
@Suite struct YearsTests {
    @Test func labelsAllYearsAndPlainYears() {
        #expect(Years.label(Years.all) == "All Years")
        #expect(Years.label(1998) == "1998")
    }

    @Test func readsAStoredYearAllYearsIncludedAndNothingElse() {
        #expect(Years.parse("1998") == 1998)
        #expect(Years.parse("0") == Years.all)
        #expect(Years.parse(nil) == nil)
        #expect(Years.parse("") == nil)
        #expect(Years.parse("1989") == nil)
        #expect(Years.parse("2011") == nil)
        #expect(Years.parse("junk") == nil)
        #expect(Years.parse("-1") == nil)
        #expect(Years.parse(" 1998") == nil)
    }

    @Test func validatesNumbers() {
        #expect(Years.valid(1990) == 1990)
        #expect(Years.valid(2010) == 2010)
        #expect(Years.valid(0) == 0)
        #expect(Years.valid(2011) == nil)
        #expect(Years.defaultYear == 1998)
        #expect(Years.pickable == Array(1990...2010) + [0])
    }
}

/// web/src/lib/channels.test.ts
@Suite struct ChannelsTests {
    let dial = ["ktvu", "nbc", "kpix", "kqed"]

    @Test func movesUpAndDownTheDial() {
        #expect(Channels.step(dial, from: "nbc", by: 1) == "kpix")
        #expect(Channels.step(dial, from: "nbc", by: -1) == "ktvu")
    }

    @Test func wrapsAtTheEndsLikeACableBox() {
        #expect(Channels.step(dial, from: "kqed", by: 1) == "ktvu")
        #expect(Channels.step(dial, from: "ktvu", by: -1) == "kqed")
    }

    @Test func startsAtTheBottomOfTheDialWhenTheChannelIsGone() {
        #expect(Channels.step(dial, from: "adult-swim", by: 1) == "ktvu")
        #expect(Channels.step([], from: "x", by: 1) == "x")
    }

    /// The dial is the on-air channels by number; off-air ones never make it (TV-13).
    @Test func dialIsOnAirChannelsByNumber() throws {
        let lineup = try APIClient.decoder.decode(Lineup.self, from: RepoFiles.fixture("channels.json"))
        var shuffled = lineup.channels
        shuffled.reverse()
        #expect(Channels.dial(shuffled).map(\.id) == ["kfix", "retro-classics", "fixture-movies"])
    }

    /// TV-33: the stored channel if it is on the dial, else the lowest-numbered one.
    @Test func startingChannel() {
        #expect(Channels.starting(dial, stored: "kpix") == "kpix")
        #expect(Channels.starting(dial, stored: "adult-swim") == "ktvu")
        #expect(Channels.starting(dial, stored: nil) == "ktvu")
        #expect(Channels.starting([], stored: "kpix") == nil)
    }
}

/// web/src/lib/focus.test.ts
@Suite struct FocusTests {
    let grid = GridSize(rows: 1, cols: 3)

    @Test func movesRightAndLeftWithinARow() {
        #expect(Focus.move(GridPoint(row: 0, col: 0), .right, in: grid) == GridPoint(row: 0, col: 1))
        #expect(Focus.move(GridPoint(row: 0, col: 2), .left, in: grid) == GridPoint(row: 0, col: 1))
    }

    @Test func stopsAtTheEdgesLikeACableBox() {
        #expect(Focus.move(GridPoint(row: 0, col: 2), .right, in: grid) == GridPoint(row: 0, col: 2))
        #expect(Focus.move(GridPoint(row: 0, col: 0), .left, in: grid) == GridPoint(row: 0, col: 0))
        #expect(Focus.move(GridPoint(row: 0, col: 1), .up, in: grid) == GridPoint(row: 0, col: 1))
    }

    @Test func movesBetweenRows() {
        let tall = GridSize(rows: 3, cols: 3)
        #expect(Focus.move(GridPoint(row: 0, col: 1), .down, in: tall) == GridPoint(row: 1, col: 1))
        #expect(Focus.move(GridPoint(row: 1, col: 1), .up, in: tall) == GridPoint(row: 0, col: 1))
    }
}

/// web/src/lib/program.test.ts
@Suite struct ProgramTests {
    static let base = GuideSlot(
        kind: .episode,
        start: Timestamp.parse("1998-10-31T20:00:00-05:00")!,
        end: Timestamp.parse("1998-10-31T20:22:00-05:00")!,
        seconds: 1320,
        title: "The Hendersons",
        show: "the-hendersons",
        episodeTitle: "Treehouse of Fixtures IX",
        season: 10,
        episode: 4,
        airDate: "1998-10-25",
        isNew: false,
        transplant: false,
        holiday: nil,
        holidayName: nil,
        description: ""
    )

    func with(_ change: (inout GuideSlot) -> Void) -> GuideSlot {
        var slot = Self.base
        change(&slot)
        return slot
    }

    @Test func kickerIsEmptyForAPlainRerun() {
        #expect(Program.kicker(Self.base).isEmpty)
    }

    @Test func kickerMarksNewAndTheHolidayGoesToTheBar() {
        let slot = with { $0.isNew = true; $0.holiday = "halloween"; $0.holidayName = "Halloween" }
        #expect(Program.kicker(slot) == [KickerPart(text: "NEW", tone: .new)])
    }

    @Test func kickerMarksMovies() {
        let movie = with { $0.kind = .movie; $0.season = nil; $0.episode = nil; $0.episodeTitle = "" }
        #expect(Program.kicker(movie) == [KickerPart(text: "MOVIE", tone: .plain)])
        var holiday = movie
        holiday.holidayName = "Halloween"
        #expect(Program.kicker(holiday) == [KickerPart(text: "MOVIE", tone: .plain)])
    }

    @Test func kickerNeverMarksBumpersOrFiller() {
        #expect(Program.kicker(with { $0.kind = .bumper; $0.isNew = true }).isEmpty)
        #expect(Program.kicker(with { $0.kind = .filler; $0.holidayName = "Halloween" }).isEmpty)
    }

    @Test func labelShowsEpisodeTitleAndSE() {
        #expect(Program.label(Self.base) == "Treehouse of Fixtures IX · S10 E4")
        #expect(Program.label(with { $0.episodeTitle = "" }) == "S10 E4")
        #expect(Program.label(with { $0.season = nil }) == "Treehouse of Fixtures IX")
    }

    @Test func labelShowsTheYearAndRunningTimeForMovies() {
        let movie = with {
            $0.kind = .movie; $0.title = "Fixtureween"; $0.season = nil; $0.episode = nil
            $0.episodeTitle = ""; $0.airDate = "1998-10-17"; $0.seconds = 84 * 60 + 30
        }
        #expect(Program.label(movie) == "1998 · 1h 24m")
        var undated = movie
        undated.airDate = nil
        #expect(Program.label(undated) == "1h 24m")
    }

    @Test func durationFormatsHoursAndMinutes() {
        #expect(Program.duration(85 * 60) == "1h 25m")
        #expect(Program.duration(22 * 60) == "22m")
        #expect(Program.duration(120 * 60) == "2h")
    }

    @Test func monthYear() {
        #expect(Program.monthYear("2004-09-07") == "Sep 2004")
    }
}

/// web/src/lib/time.test.ts
@Suite struct TimeFormatTests {
    let zone = "America/New_York"
    let t = Timestamp.parse("1998-10-31T20:14:00-05:00")!

    @Test func formatsTheClock() {
        #expect(TimeFormat.clock(t, zone: zone) == "8:14 PM")
        #expect(TimeFormat.shortTime(t, zone: zone) == "8:14")
        #expect(TimeFormat.clock(Timestamp.parse("1998-10-31T00:05:00-05:00")!, zone: zone) == "12:05 AM")
    }

    @Test func formatsTheLongDate() {
        #expect(TimeFormat.longDate(t, zone: zone) == "Saturday, Oct 31, 1998")
        #expect(TimeFormat.fullDate(t, zone: zone) == "Saturday, October 31, 1998")
    }

    @Test func formatsTheGuideDay() {
        #expect(TimeFormat.guideDay(t, zone: zone) == "Sat 10/31")
    }

    @Test func formatsAirDatesInUTC() {
        #expect(TimeFormat.airDate("1998-09-24") == "Thu, Sep 24, 1998")
        #expect(TimeFormat.airDate("junk") == nil)
    }

    /// The zone comes from the server; the device's own zone never matters.
    @Test func usesTheServerZoneNotTheDevice() {
        #expect(TimeFormat.clock(t, zone: "America/Los_Angeles") == "5:14 PM")
        #expect(TimeFormat.clock(t, zone: "Not/AZone") == "8:14 PM")
    }

    @Test func zonedPartsGiveMonthDayAndHourMinuteInTheZone() {
        #expect(TimeFormat.zonedParts(t, zone: zone) == ZonedParts(date: "10-31", time: "20:14"))
        #expect(
            TimeFormat.zonedParts(Timestamp.parse("1998-11-01T04:30:00Z")!, zone: zone)
                == ZonedParts(date: "10-31", time: "23:30")
        )
    }
}

/// web/src/lib/time.test.ts (simulatedNow)
@Suite struct SimulatedTimeTests {
    @Test func advancesTheServersSimulatedTimeByRealElapsedTime() {
        let server = Timestamp.parse("1998-10-31T20:14:00-05:00")!
        let anchor = SimulatedTime(serverNow: server, receivedAt: 1.0)
        #expect(anchor.now(at: 61.0) == server.addingTimeInterval(60))
    }
}

/// web/src/lib/guide.test.ts
@Suite struct GuideLayoutTests {
    static func T(_ hhmmss: String) -> Date { Timestamp.parse("1998-10-31T\(hhmmss)-05:00")! }

    static func slot(_ kind: SlotKind, _ start: String, _ end: String, _ title: String? = nil) -> GuideSlot {
        GuideSlot(
            kind: kind, start: T(start), end: T(end), seconds: Int(T(end).timeIntervalSince(T(start))),
            title: title ?? kind.rawValue, show: nil, episodeTitle: "", season: nil, episode: nil, airDate: nil,
            isNew: false, transplant: false, holiday: nil, holidayName: nil, description: ""
        )
    }

    let slots = [
        slot(.episode, "19:52:00", "20:14:00", "Nova Fixture"),
        slot(.bumper, "20:14:00", "20:14:10"),
        slot(.episode, "20:14:10", "20:36:10", "Arthur Fixture"),
        slot(.bumper, "20:36:10", "20:36:20"),
        slot(.movie, "20:36:20", "22:06:20", "Fixtureween"),
    ]

    @Test func cellSizeDropsTextAsCellsGetNarrow() {
        let area = TVLayout.Guide.cellsWidth
        #expect(GuideLayout.cellSize(1.0 / 3.0) == .full)
        #expect(GuideLayout.cellSize(100 / area) == .narrow)
        #expect(GuideLayout.cellSize(119.9 / area) == .narrow)
        #expect(GuideLayout.cellSize(120 / area) == .full)
        #expect(GuideLayout.cellSize(39 / area) == .sliver)
        #expect(GuideLayout.cellSize(0.5 / 90) == .sliver)
    }

    @Test func hidesBumpersInsideTheNextProgramsCellAndSizesCellsByRunTime() throws {
        let cells = GuideLayout.row(slots, from: Self.T("20:00:00"), to: Self.T("21:30:00"))
        #expect(cells.map(\.slot.title) == ["Nova Fixture", "Arthur Fixture", "Fixtureween"])
        let nova = cells[0], arthur = cells[1], movie = cells[2]
        #expect(nova.left == 0)
        #expect(nova.clippedStart)
        #expect(nova.end == Self.T("20:14:10"))
        #expect(abs(arthur.left - Double(14 * 60 + 10) / 5400) < 1e-9)
        #expect(!arthur.clippedStart)
        #expect(abs(movie.left + movie.width - 1) < 1e-9)
    }

    @Test func dropsCellsOutsideTheWindow() {
        let cells = GuideLayout.row(slots, from: Self.T("21:00:00"), to: Self.T("22:30:00"))
        #expect(cells.map(\.slot.title) == ["Fixtureween"])
    }

    @Test func findsTheCellCoveringATimeOrTheNearest() {
        let cells = GuideLayout.row(slots, from: Self.T("20:00:00"), to: Self.T("21:30:00"))
        #expect(GuideLayout.cellIndex(cells, at: Self.T("20:20:00")) == 1)
        #expect(GuideLayout.cellIndex(cells, at: Self.T("23:00:00")) == 2)
        #expect(GuideLayout.cellIndex(cells, at: Self.T("18:00:00")) == 0)
        #expect(GuideLayout.cellIndex([], at: Self.T("18:00:00")) == 0)
    }

    @Test func windowStartRoundsDownToTheHalfHour() {
        #expect(GuideLayout.windowStart(for: Self.T("20:14:33")) == Self.T("20:00:00"))
        #expect(GuideLayout.windowStart(for: Self.T("20:44:00")) == Self.T("20:30:00"))
        #expect(GuideLayout.window == 5400)
    }
}
