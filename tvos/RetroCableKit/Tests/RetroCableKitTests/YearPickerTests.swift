import CoreGraphics
import Foundation
import Testing
@testable import RetroCableKit

/// A /channels source the picker tests control: records each year asked, answers or fails.
@MainActor
final class LineupSource {
    private(set) var asked: [Int] = []
    var failing: Set<Int> = []

    var fetch: @Sendable (Int) async throws -> Lineup {
        { [self] year in try await self.answer(year) }
    }

    private func answer(_ year: Int) async throws -> Lineup {
        asked.append(year)
        if failing.contains(year) { throw APIError.unreachable }
        return try YearPickerTests.lineup(year: year)
    }
}

/// The year picker (web/src/screens/YearPicker.tsx; TV-29, TV-30, D-10).
@MainActor
@Suite struct YearPickerTests {
    nonisolated static func lineup(year: Int, date: String? = nil) throws -> Lineup {
        var lineup = try APIClient.decoder.decode(Lineup.self, from: RepoFiles.fixture("channels.json"))
        lineup.year = year
        lineup.date = date ?? (year == Years.all ? "2026-11-11" : "\(year)-11-11")
        // The server sends the weekday; work it out the same way for the made-up dates.
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyy-MM-dd"
        let day = formatter.date(from: lineup.date)!
        formatter.dateFormat = "EEEE"
        lineup.weekday = formatter.string(from: day)
        return lineup
    }

    // MARK: - Grid moves (TV-29)

    @Test func yearsFillThreeRowsOfSevenAndAllYearsHasTheFourth() {
        #expect(YearGrid.position(1990) == GridPoint(row: 0, col: 0))
        #expect(YearGrid.position(1996) == GridPoint(row: 0, col: 6))
        #expect(YearGrid.position(1997) == GridPoint(row: 1, col: 0))
        #expect(YearGrid.position(2010) == GridPoint(row: 2, col: 6))
        #expect(YearGrid.position(Years.all) == GridPoint(row: 3, col: 0))
        #expect(YearGrid.rows == [Array(1990...1996), Array(1997...2003), Array(2004...2010), [0]])
    }

    @Test func movesClampAtTheEdges() {
        #expect(YearGrid.move(1998, .right) == 1999)
        #expect(YearGrid.move(1998, .down) == 2005)
        #expect(YearGrid.move(1990, .left) == 1990)
        #expect(YearGrid.move(1990, .up) == 1990)
        #expect(YearGrid.move(1996, .right) == 1996)
        #expect(YearGrid.move(2003, .right) == 2003)
    }

    @Test func downFromTheThirdRowReachesAllYearsAndUpReturns() {
        #expect(YearGrid.move(2004, .down) == Years.all)
        #expect(YearGrid.move(2010, .down) == Years.all)
        #expect(YearGrid.move(Years.all, .down) == Years.all)
        #expect(YearGrid.move(Years.all, .left) == Years.all)
        #expect(YearGrid.move(Years.all, .right) == Years.all)
        #expect(YearGrid.move(Years.all, .up) == 2004)
    }

    @Test func anUnknownYearMovesFromTheStart() {
        #expect(YearGrid.move(1985, .right) == 1991)
    }

    // MARK: - Panel text (TV-30)

    @Test func panelWhileLoadingShowsTheYearLabel() {
        let panel = YearPanel.make(year: 1998, lineup: nil)
        #expect(panel.kicker == "Today on your TV is")
        #expect(panel.date == "1998")
        #expect(panel.sub == "Each channel plays what had aired by this date, new episodes and reruns mixed.")
        #expect(panel.dialTitle == "On the dial in 1998")
        #expect(panel.dial == nil)
        #expect(YearPanel.make(year: 0, lineup: nil).date == "All Years")
    }

    @Test func panelShowsTheLongDateAndTheDial() throws {
        let panel = YearPanel.make(year: 1998, lineup: try Self.lineup(year: 1998))
        #expect(panel.date == "Wednesday, November 11, 1998")
        let dial = try #require(panel.dial)
        #expect(dial.map(\.number) == ["02", "07", "31", "40"])
        #expect(dial.map(\.name) == ["KFIX (Fixture)", "Retro Classics", "Late Toons", "Fixture Movies"])
        #expect(dial.map(\.note) == ["On air", "Always on (classics)", "Hidden until 2001", "Your playlist"])
        #expect(dial.map(\.onAir) == [true, true, false, true])
    }

    @Test func allYearsHasItsOwnCopyAndTheRealDate() throws {
        let panel = YearPanel.make(year: 0, lineup: try Self.lineup(year: 0))
        #expect(panel.date == "Wednesday, November 11, 2026")
        #expect(panel.sub == "All Years: every channel on, airing every season of every show, whenever it first aired.")
        #expect(panel.dialTitle == "On the dial in All Years")
    }

    @Test func aChannelWithNoLaunchDateIsHiddenUntilLater() {
        var channel = Make.channel("x", number: 9, onAir: false)
        channel.launch = nil
        #expect(DialEntry(channel).note == "Hidden until later")
    }

    // MARK: - The dial in two columns (D-10)

    static func entries(_ count: Int) -> [DialEntry] {
        (0..<count).map { DialEntry(Make.channel("c\($0)", number: $0 + 2)) }
    }

    @Test func twentyFourChannelsFitTwoColumnsOfTwelve() {
        let layout = DialColumns.make(Self.entries(24))
        #expect(layout.columns.map(\.count) == [12, 12])
        #expect(layout.rowHeight == TVLayout.YearPicker.dialRowHeight)
        #expect(layout.more == 0)
        #expect(layout.columns[1].first?.number == "14")
        #expect(CGFloat(12) * layout.rowHeight <= TVLayout.YearPicker.dialListHeight)
    }

    @Test func fewerChannelsFillTheFirstColumnFirst() {
        let layout = DialColumns.make(Self.entries(14))
        #expect(layout.columns.map(\.count) == [12, 2])
        #expect(DialColumns.make([]).columns.map(\.count) == [0, 0])
    }

    @Test func moreChannelsShrinkTheRowsToFortyFour() {
        let layout = DialColumns.make(Self.entries(30))
        #expect(layout.columns.map(\.count) == [15, 15])
        #expect(layout.rowHeight >= TVLayout.YearPicker.dialRowMinHeight)
        #expect(layout.rowHeight < TVLayout.YearPicker.dialRowHeight)
        #expect(CGFloat(15) * layout.rowHeight <= TVLayout.YearPicker.dialListHeight)
    }

    @Test func beyondThatTheLastRowReadsMore() {
        let perColumn = Int(TVLayout.YearPicker.dialListHeight / TVLayout.YearPicker.dialRowMinHeight)
        let layout = DialColumns.make(Self.entries(2 * perColumn + 5))
        #expect(layout.rowHeight >= TVLayout.YearPicker.dialRowMinHeight)
        #expect(CGFloat(perColumn) * layout.rowHeight <= TVLayout.YearPicker.dialListHeight)
        #expect(layout.columns.map(\.count) == [perColumn, perColumn - 1])
        #expect(layout.more == 6)
        #expect(layout.moreText == "+6 more")
    }

    // MARK: - Fetching (TV-30)

    func picker(_ source: LineupSource, clock: ManualClock, year: Int = 1998) -> YearPickerModel {
        YearPickerModel(current: year, timers: clock, fetch: source.fetch)
    }

    @Test func focusStartsOnTheCurrentYearAndFetches120msLater() async {
        let source = LineupSource()
        let clock = ManualClock()
        let picker = picker(source, clock: clock, year: 2001)
        picker.start()
        #expect(picker.focused == 2001)
        #expect(picker.current == 2001)
        clock.advance(0.11)
        await settle()
        #expect(source.asked.isEmpty)
        clock.advance(0.02)
        await settle()
        #expect(source.asked == [2001])
        #expect(picker.panel.date == "Sunday, November 11, 2001")
    }

    @Test func eachYearIsFetchedOnceWhileThePickerIsOpen() async {
        let source = LineupSource()
        let clock = ManualClock()
        let picker = picker(source, clock: clock)
        picker.start()
        clock.advance(0.2)
        await settle()
        picker.move(.right)
        #expect(picker.focused == 1999)
        #expect(picker.panel.dial == nil)
        clock.advance(0.2)
        await settle()
        picker.move(.left)
        #expect(picker.panel.dial != nil)
        clock.advance(1)
        await settle()
        #expect(source.asked == [1998, 1999])
    }

    @Test func passingOverAYearQuicklyNeverFetchesIt() async {
        let source = LineupSource()
        let clock = ManualClock()
        let picker = picker(source, clock: clock)
        picker.start()
        picker.move(.right)
        clock.advance(0.05)
        picker.move(.right)
        clock.advance(0.05)
        picker.move(.right)
        clock.advance(0.2)
        await settle()
        #expect(source.asked == [2001])
    }

    @Test func failuresRetryAfterOneTwoUpToFiveSeconds() async {
        let source = LineupSource()
        source.failing = [1998]
        let clock = ManualClock()
        let picker = picker(source, clock: clock)
        picker.start()
        clock.advance(0.12)
        await settle()
        var gaps: [Double] = []
        for _ in 0..<7 {
            gaps.append(((clock.waiting.first ?? -1) * 1000).rounded() / 1000)
            clock.advance(clock.waiting.first ?? 0)
            await settle()
        }
        #expect(gaps == [1, 2, 3, 4, 5, 5, 5], "\(gaps)")
        #expect(source.asked.count == 8)
        source.failing = []
        clock.advance(5)
        await settle()
        #expect(picker.panel.dial != nil)
        #expect(clock.waiting.isEmpty)
    }

    @Test func movingResetsTheRetryDelay() async {
        let source = LineupSource()
        source.failing = [1998, 1999]
        let clock = ManualClock()
        let picker = picker(source, clock: clock)
        picker.start()
        clock.advance(0.12)
        await settle()
        clock.advance(1)
        await settle()
        #expect(clock.waiting == [2])
        picker.move(.right)
        #expect(clock.waiting.map { ($0 * 1000).rounded() } == [120])
    }

    @Test func closingStopsEveryTimer() async {
        let source = LineupSource()
        source.failing = [1998]
        let clock = ManualClock()
        let picker = picker(source, clock: clock)
        picker.start()
        clock.advance(0.12)
        await settle()
        #expect(!clock.waiting.isEmpty)
        picker.close()
        #expect(clock.waiting.isEmpty)
    }
}
