import Foundation
import Testing
@testable import RetroCableKit

/// The guide and the year picker in the app model: /guide fetch and retry (TV-22), the remote in
/// the guide (TV-24, TV-25, TV-28), the year picker (TV-29 to TV-31), reporting (TV-35) and the
/// background (TV-37). The rig's lineup has kfix (02), retro-classics (07) and fixture-movies (40)
/// on the dial; its guide runs half-hour shows on each from 7:30 PM.
@MainActor
@Suite struct GuideModelTests {
    func guideRequests(_ rig: LiveRig) -> [URLComponents] {
        rig.transport.requests.compactMap { request in
            guard request.url?.path == "/api/guide" else { return nil }
            return URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
        }
    }

    /// Turned on (now 7:35 PM on kfix) with the guide open and loaded.
    func inTheGuide(_ rig: LiveRig) async {
        await rig.turnedOn()
        rig.app.pressSelect()
        await until { rig.app.guideStatus != .loading }
    }

    // MARK: - Opening (TV-22, TV-24)

    @Test func openingTheGuideAsksForThirteenAndAHalfHoursFromTheHalfHour() async throws {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.app.pressSelect()
        #expect(rig.app.screen == .guide)
        #expect(rig.app.guideStatus == .loading)
        #expect(rig.app.guideDetails() == nil)
        await until { rig.app.guideStatus != .loading }
        #expect(rig.app.guideStatus == .loaded)
        let query = try #require(guideRequests(rig).first?.queryItems)
        #expect(query.contains(URLQueryItem(name: "year", value: "1998")))
        #expect(query.contains(URLQueryItem(name: "date", value: "11-11")))
        #expect(query.contains(URLQueryItem(name: "start", value: "19:30")))
        #expect(query.contains(URLQueryItem(name: "hours", value: "13.5")))
        let grid = try #require(rig.app.guide)
        #expect(grid.opened == Make.base)
        #expect(grid.rows.map(\.channel.id) == ["kfix", "retro-classics", "fixture-movies"])
        #expect(grid.focus.row == 0)
        #expect(grid.focusedCell?.slot.title == "kfix 1")
        #expect(rig.app.guideDetails()?.title == "kfix 1")
        #expect(rig.app.guideDetails()?.line == "02 · KFIX FOX · 7:30 – 8:00 PM")
    }

    @Test func focusOpensOnTheTunedChannel() async {
        let rig = LiveRig(channel: "fixture-movies")
        await inTheGuide(rig)
        #expect(rig.app.guide?.focus.row == 2)
        #expect(rig.app.guide?.focusedRow?.channel.id == "fixture-movies")
    }

    @Test func aFailedGuideReadsNoSignalAndRetriesEveryFiveSeconds() async {
        let rig = LiveRig()
        rig.guideFeed.failing = true
        await inTheGuide(rig)
        #expect(rig.app.guideStatus == .failed)
        #expect(guideRequests(rig).count == 1)
        rig.clock.advance(4.9)
        await settle()
        #expect(guideRequests(rig).count == 1)
        rig.clock.advance(0.1)
        await until { guideRequests(rig).count == 2 }
        await settle()
        #expect(rig.app.guideStatus == .failed)
        rig.guideFeed.failing = false
        rig.clock.advance(5)
        await until { rig.app.guideStatus == .loaded }
        #expect(guideRequests(rig).count == 3)
        #expect(rig.app.guideDetails()?.title == "kfix 1")
    }

    @Test func closingTheGuideStopsItsRetries() async {
        let rig = LiveRig()
        rig.guideFeed.failing = true
        await inTheGuide(rig)
        rig.app.pressBack()
        rig.clock.advance(30)
        await settle()
        #expect(guideRequests(rig).count == 1)
    }

    // MARK: - The remote in the guide (TV-24, TV-25, TV-28)

    @Test func arrowsMoveTheGuideFocusNotTheChannel() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        rig.app.move(.down)
        rig.app.move(.right)
        #expect(rig.app.guide?.focus.row == 1)
        #expect(rig.app.guide?.focusedCell?.slot.title == "retro-classics 2")
        #expect(rig.app.guideDetails()?.line == "07 · RTC · 8:00 – 8:30 PM")
        await settle()
        #expect(rig.app.channelID == "kfix")
        #expect(rig.server.requests.count == 1)
        #expect(rig.app.screen == .guide)
    }

    @Test func selectTunesTheFocusedRowAndReturnsWithTheBanner() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        rig.clock.advance(5)
        #expect(!rig.app.bannerVisible)
        rig.app.move(.down)
        rig.app.move(.down)
        rig.app.move(.right)
        await rig.press { rig.app.pressSelect() }
        #expect(rig.app.screen == .live)
        #expect(rig.app.channelID == "fixture-movies")
        #expect(rig.server.requests.last == .init(channel: "fixture-movies", year: 1998))
        #expect(rig.app.bannerVisible)
        #expect(rig.app.lastChannelID == "kfix")
        #expect(rig.app.guide == nil)
    }

    @Test func selectOnTheTunedChannelReturnsWithoutARetune() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        rig.clock.advance(5)
        rig.app.pressSelect()
        await settle()
        #expect(rig.app.screen == .live)
        #expect(rig.server.requests.count == 1)
        #expect(rig.app.bannerVisible)
    }

    @Test func selectBeforeTheGuideLoadsDoesNothing() async {
        let rig = LiveRig()
        rig.guideFeed.failing = true
        await inTheGuide(rig)
        rig.app.pressSelect()
        #expect(rig.app.screen == .guide)
    }

    @Test func reopeningTheGuideRecentersIt() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        for _ in 0..<6 { rig.app.move(.right) }
        #expect(rig.app.guide?.windowStart != Make.base)
        rig.app.pressBack()
        rig.app.pressSelect()
        await until { rig.app.guideStatus == .loaded }
        #expect(rig.app.guide?.windowStart == Make.base)
        #expect(rig.app.guide?.focusedCell?.slot.title == "kfix 1")
        #expect(guideRequests(rig).count == 2)
    }

    // MARK: - Year picker (TV-29 to TV-31, TV-35)

    @Test func playPauseInTheGuideOpensTheYearPickerAndSilencesThePicture() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        rig.engine.play(duration: 30 * 60)
        rig.clock.advance(30)
        rig.app.pressPlayPause()
        #expect(rig.app.screen == .yearPicker)
        #expect(rig.app.yearPicker?.focused == 1998)
        #expect(rig.app.yearPicker?.current == 1998)
        #expect(rig.engine.calls.last == .stop)
        #expect(rig.app.tuner.slot == nil)
        #expect(!rig.app.isPlaying)
        #expect(!rig.app.backLeavesApp)
        #expect(rig.reports.last?.ended == .tuneAway)
    }

    @Test func theYearPickerFetchesTheFocusedYearsLineup() async {
        let rig = LiveRig(lineups: [1999: ["late-toons": true]])
        await inTheGuide(rig)
        rig.app.pressPlayPause()
        let lineups = rig.channelRequests
        rig.app.move(.right)
        #expect(rig.app.yearPicker?.focused == 1999)
        rig.clock.advance(0.12)
        await rig.waitForChannels(lineups + 1)
        await until { rig.app.yearPicker?.panel.dial != nil }
        #expect(rig.app.yearPicker?.panel.dial?.map(\.onAir) == [true, true, true, true])
    }

    @Test func backFromTheYearPickerReturnsToTheGuideAsItWasAtTheLivePosition() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        rig.app.move(.down)
        rig.app.move(.right)
        let before = rig.app.guide
        rig.app.pressPlayPause()
        rig.app.move(.left)
        rig.app.pressBack()
        #expect(rig.app.screen == .guide)
        #expect(rig.app.guide == before)
        #expect(rig.app.yearPicker == nil)
        #expect(rig.app.year == 1998)
        await rig.server.waitFor(2)
        #expect(rig.server.requests.last == .init(channel: "kfix", year: 1998))
        #expect(rig.app.isPlaying)
        #expect(Settings(rig.store).year == 1998)
    }

    @Test func selectInTheYearPickerSetsTheYearAndRetunes() async {
        let rig = LiveRig(channel: "late-toons", lineups: [2005: ["late-toons": true]])
        await inTheGuide(rig)
        rig.app.pressPlayPause()
        rig.app.move(.down)
        #expect(rig.app.yearPicker?.focused == 2005)
        rig.app.pressSelect()
        await rig.app.changingYear?.value
        await rig.server.waitFor(2)
        #expect(rig.app.screen == .live)
        #expect(rig.app.year == 2005)
        #expect(Settings(rig.store).year == 2005)
        #expect(rig.server.requests.last == .init(channel: "late-toons", year: 2005))
        #expect(rig.app.bannerVisible)
        #expect(rig.app.yearPicker == nil)
        #expect(rig.app.guide == nil)
    }

    @Test func pickingAllYearsStoresZero() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        rig.app.pressPlayPause()
        for _ in 0..<3 { rig.app.move(.down) }
        #expect(rig.app.yearPicker?.focused == Years.all)
        rig.app.pressSelect()
        await rig.app.changingYear?.value
        #expect(rig.app.year == 0)
        #expect(Settings(rig.store).year == 0)
    }

    // MARK: - Background (TV-37)

    @Test func aGuideOlderThanThirtyMinutesReopensAtTheCurrentHalfHour() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        rig.app.move(.down)
        rig.app.enterBackground()
        #expect(rig.clock.waiting.isEmpty)
        rig.clock.advance(60 * 60)
        rig.app.enterForeground()
        #expect(rig.app.screen == .guide)
        // 7:35 PM + 1 h: the guide opens again at 8:30 on the tuned channel.
        #expect(rig.app.guide?.opened == Make.base.addingTimeInterval(60 * 60))
        await until { rig.app.guideStatus == .loaded }
        #expect(rig.app.guide?.focus.row == 0)
        #expect(guideRequests(rig).count == 2)
    }

    @Test func aRecentGuideIsKeptAfterTheBackground() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        rig.app.move(.down)
        let before = rig.app.guide
        rig.app.enterBackground()
        rig.clock.advance(10 * 60)
        rig.app.enterForeground()
        #expect(rig.app.guide == before)
        await settle()
        #expect(guideRequests(rig).count == 1)
    }

    @Test func theYearPickerStaysHiddenAndSilentAfterTheBackground() async {
        let rig = LiveRig()
        await inTheGuide(rig)
        rig.app.pressPlayPause()
        rig.app.enterBackground()
        #expect(rig.clock.waiting.isEmpty)
        let lineups = rig.channelRequests
        rig.app.enterForeground()
        await rig.waitForChannels(lineups + 1)
        await settle()
        #expect(rig.app.screen == .yearPicker)
        #expect(rig.server.requests.count == 1)
    }
}
