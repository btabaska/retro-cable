import Foundation
import Testing
@testable import RetroCableKit

/// Live TV in the app model: the remote (TV-13 to TV-17), the banner timer (TV-18), persisted
/// channel and year (TV-32, TV-33), watch reporting wired to playback (TV-34 to TV-36), the
/// background (TV-37) and simulated midnight (TV-38).
@MainActor
final class LiveRig {
    let clock = ManualClock()
    let engine = FakeEngine()
    let server = TuneServer()
    let transport: StubTransport
    let guideFeed = GuideFeed()
    /// Overrides the lineup's `now` (ISO 8601), as after a sleep the monotonic clock didn't see.
    let lineupNow = LockedValue<String?>(nil)
    let store: UserDefaults
    var reports: [WatchReport] = []
    var logs: [String] = []
    lazy var app = AppModel(
        api: APIClient(baseURL: URL(string: "https://tv.example.test")!, transport: transport),
        settings: Settings(store),
        sleeper: RecordingSleeper(),
        engine: engine,
        clock: clock,
        timers: clock,
        tuneFetch: server.fetch,
        sendWatch: { [unowned self] in self.reports.append($0) },
        log: { [unowned self] in self.logs.append($0) }
    )

    /// `lineups` maps a year to channel overrides (id -> on_air) on top of channels.json.
    init(year: Any? = nil, channel: String? = nil, lineups: [Int: [String: Bool]] = [:]) {
        let name = "rc-live-\(UUID().uuidString)"
        store = UserDefaults(suiteName: name)!
        store.removePersistentDomain(forName: name)
        if let year { store.set(year, forKey: Settings.yearKey) }
        if let channel { store.set(channel, forKey: Settings.channelKey) }
        let feed = guideFeed
        let now = lineupNow
        transport = StubTransport { request in
            let url = request.url!
            switch url.path {
            case "/api/guide": return try feed.answer()
            case "/api/channels":
                let year = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?
                    .first { $0.name == "year" }?.value.flatMap(Int.init) ?? 1998
                return (200, try LiveRig.lineup(year: year, onAir: lineups[year] ?? [:], now: now.value))
            case "/api/version": return (200, try RepoFiles.fixture("version.json"))
            default: return (404, Data())
            }
        }
    }

    nonisolated static func lineup(year: Int, onAir: [String: Bool], now: String? = nil) throws -> Data {
        var json = try JSONSerialization.jsonObject(with: RepoFiles.fixture("channels.json")) as! [String: Any]
        json["year"] = year
        if let now { json["now"] = now }
        var channels = json["channels"] as! [[String: Any]]
        for i in channels.indices {
            if let value = onAir[channels[i]["id"] as! String] { channels[i]["on_air"] = value }
        }
        json["channels"] = channels
        return try JSONSerialization.data(withJSONObject: json)
    }

    var channelRequests: Int {
        transport.requests.filter { $0.url?.path == "/api/channels" }.count
    }

    func channel(_ id: String) -> ChannelInfo {
        app.dial.first { $0.id == id }!
    }

    /// Turns on and answers the first tune with an evening whose `now` is 5 minutes in.
    func turnedOn() async {
        app.pressPower()
        await app.turningOn?.value
        await server.waitFor(1)
        answer(now: 5)
        await app.tuner.settled()
    }

    func answer(now: Double = 5, _ slots: [TunedSlot]? = nil) {
        let channel = app.tuner.channel!
        server.answerLast(Make.tune(channel, now: now, slots ?? Make.evening(channel.id)))
    }

    /// Presses, then answers the tune the press started.
    func press(_ action: () -> Void, now: Double = 5) async {
        let before = server.requests.count
        action()
        await server.waitFor(before + 1)
        if server.requests.count > before {
            answer(now: now)
            await app.tuner.settled()
        }
    }

    func waitForChannels(_ count: Int) async {
        for _ in 0..<10_000 where channelRequests < count {
            await Task.yield()
        }
    }
}

@MainActor
@Suite struct LiveModelTests {
    /// TV-4, TV-18: power on tunes the stored channel and shows the banner for 4 s of real time.
    @Test func turningOnTunesTheStoredChannelWithTheBanner() async {
        let rig = LiveRig(channel: "fixture-movies")
        await rig.turnedOn()
        #expect(rig.app.screen == .live)
        #expect(rig.server.requests == [.init(channel: "fixture-movies", year: 1998)])
        #expect(rig.app.bannerVisible)
        rig.clock.advance(3.9)
        #expect(rig.app.bannerVisible)
        rig.clock.advance(0.2)
        #expect(!rig.app.bannerVisible)
    }

    /// TV-4, design 9: the banner's 4 s start when the TV-on opening finishes, however long the
    /// opening took (a slow first launch once held the line past the whole 4 s, and no banner showed).
    @Test func theBannerHoldsFourSecondsAfterTheOpeningFinishes() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.clock.advance(5)
        #expect(!rig.app.bannerVisible)
        rig.app.tvOnOpened()
        #expect(rig.app.bannerVisible)
        rig.clock.advance(3.9)
        #expect(rig.app.bannerVisible)
        rig.clock.advance(0.2)
        #expect(!rig.app.bannerVisible)
    }

    /// Design 9: an opening that finishes after the guide opened doesn't restart the banner.
    @Test func theOpeningFinishingOffLiveTVShowsNoBanner() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.app.pressSelect()
        #expect(rig.app.screen == .guide)
        rig.clock.advance(5)
        rig.app.tvOnOpened()
        rig.app.pressBack()
        #expect(rig.app.screen == .live)
        #expect(!rig.app.bannerVisible)
    }

    /// TV-18: every trigger restarts the 4 s.
    @Test func eachTriggerRestartsTheBanner() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.clock.advance(3)
        rig.app.move(.right)
        rig.clock.advance(3)
        #expect(rig.app.bannerVisible)
        rig.clock.advance(1.1)
        #expect(!rig.app.bannerVisible)
        rig.app.move(.left)
        #expect(rig.app.bannerVisible)
    }

    /// TV-13: up and down step the dial, wrapping and skipping off-air channels; one press, one
    /// channel, and the channel is saved.
    @Test func upAndDownStepTheDial() async {
        let rig = LiveRig()
        await rig.turnedOn()
        #expect(rig.app.channelID == "kfix")
        await rig.press { rig.app.move(.down) }
        #expect(rig.app.channelID == "fixture-movies")
        await rig.press { rig.app.move(.up) }
        #expect(rig.app.channelID == "kfix")
        await rig.press { rig.app.move(.up) }
        #expect(rig.app.channelID == "retro-classics")
        await rig.press { rig.app.move(.up) }
        #expect(rig.app.channelID == "fixture-movies")
        #expect(rig.server.requests.map(\.channel) == ["kfix", "fixture-movies", "kfix", "retro-classics", "fixture-movies"])
        #expect(Settings(rig.store).channel == "fixture-movies")
        #expect(rig.app.bannerVisible)
    }

    /// TV-14, TV-40: each press tunes at once with no debounce; only the last one plays.
    @Test func rapidPressesEachTuneAtOnce() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.engine.play(duration: 23 * 60)
        for i in 1...5 {
            rig.app.move(.up)
            #expect(rig.engine.calls.last == .stop, "press \(i) stops the old picture at once")
            await rig.server.waitFor(1 + i)
        }
        #expect(rig.server.requests.count == 6)
        #expect(rig.app.channelID == "fixture-movies")
        #expect(rig.app.tuner.channel?.id == "fixture-movies")
    }

    /// TV-15: Play/Pause toggles to the previous channel and back; nothing without one.
    @Test func playPauseIsLastChannel() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.app.pressPlayPause()
        await settle()
        #expect(rig.server.requests.count == 1)
        await rig.press { rig.app.move(.up) }
        #expect(rig.app.channelID == "retro-classics")
        await rig.press { rig.app.pressPlayPause() }
        #expect(rig.app.channelID == "kfix")
        await rig.press { rig.app.pressPlayPause() }
        #expect(rig.app.channelID == "retro-classics")
        #expect(rig.app.lastChannelID == "kfix")
    }

    @Test func lastChannelOffTheDialDoesNothing() async {
        let rig = LiveRig(lineups: [2001: ["retro-classics": false]])
        await rig.turnedOn()
        await rig.press { rig.app.move(.up) }
        await rig.press { rig.app.move(.up) }
        #expect(rig.app.channelID == "fixture-movies")
        #expect(rig.app.lastChannelID == "retro-classics")
        // In 2001 retro-classics is off the dial, so Play/Pause has nowhere to go.
        await rig.app.changeYear(2001)
        await rig.server.waitFor(4)
        rig.answer()
        await rig.app.tuner.settled()
        #expect(rig.app.channelID == "fixture-movies")
        rig.app.pressPlayPause()
        await settle()
        #expect(rig.server.requests.count == 4)
        #expect(rig.app.channelID == "fixture-movies")
    }

    /// TV-16, TV-28: Select opens the guide; Back returns without retuning.
    @Test func selectOpensTheGuideAndBackReturnsWithoutARetune() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.app.pressSelect()
        #expect(rig.app.screen == .guide)
        rig.app.pressBack()
        #expect(rig.app.screen == .live)
        await settle()
        #expect(rig.server.requests.count == 1)
    }

    /// TV-17: Back shows the banner when it is hidden; when it shows, Back leaves the app.
    @Test func backShowsTheBannerThenLeaves() async {
        let rig = LiveRig()
        await rig.turnedOn()
        #expect(rig.app.backLeavesApp)
        rig.clock.advance(5)
        #expect(!rig.app.backLeavesApp)
        rig.app.pressBack()
        #expect(rig.app.bannerVisible)
        #expect(rig.app.backLeavesApp)
    }

    /// TV-12: a failed tune keeps the channel and the banner reads "Tuning…".
    @Test func aFailedTuneReadsTuning() async {
        let rig = LiveRig()
        rig.app.pressPower()
        await rig.app.turningOn?.value
        await rig.server.waitFor(1)
        rig.server.failLast()
        await rig.app.tuner.settled()
        #expect(rig.app.screen == .live)
        #expect(rig.app.bannerContent()?.title == "Tuning…")
        #expect(rig.app.bannerContent()?.number == "02")
        rig.clock.advance(5)
        await rig.server.waitFor(2)
        #expect(rig.server.requests.count == 2)
    }

    /// TV-19, TV-21: the banner and clock read the tuner's slot on simulated time.
    @Test func bannerAndClockFollowSimulatedTime() async {
        let rig = LiveRig()
        await rig.turnedOn()
        #expect(rig.app.bannerContent()?.title == "kfix 1")
        #expect(rig.app.bannerContent()?.next?.title == "kfix 2")
        #expect(rig.app.clockText() == ClockText(time: "7:35 PM", date: "Wednesday, Nov 11, 1998"))
        rig.clock.advance(60)
        #expect(rig.app.clockText().time == "7:36 PM")
    }

    /// TV-32, TV-33: the stored channel off the dial plays the lowest and stays stored; a year
    /// where it is on the dial tunes it again; the year is saved.
    @Test func storedChannelComesBackInAYearWhereItIsOnTheDial() async {
        let rig = LiveRig(channel: "late-toons", lineups: [2005: ["late-toons": true]])
        await rig.turnedOn()
        #expect(rig.app.channelID == "kfix")
        #expect(Settings(rig.store).channel == "late-toons")
        await rig.app.changeYear(2005)
        await rig.server.waitFor(2)
        #expect(rig.server.requests.last == .init(channel: "late-toons", year: 2005))
        #expect(rig.app.year == 2005)
        #expect(Settings(rig.store).year == 2005)
        #expect(rig.app.bannerVisible)
    }

    @Test func allYearsIsStoredAsZero() async {
        let rig = LiveRig(year: 0)
        #expect(rig.app.year == 0)
        #expect(rig.app.placeLine == "Mountain View, CA · All Years")
        await rig.turnedOn()
        #expect(rig.server.requests.first?.year == 0)
    }

    /// TV-33: last channel is not saved across launches.
    @Test func lastChannelStartsEmpty() async {
        let rig = LiveRig(channel: "retro-classics")
        await rig.turnedOn()
        #expect(rig.app.lastChannelID == nil)
    }

    // MARK: - Watch reporting

    /// TV-34, TV-35: one session per program, heartbeats every 60 s, program_end at the next
    /// program, tune_away on a channel change.
    @Test func reportsWhatPlays() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.clock.advance(60)
        #expect(rig.reports.count == 1)
        let first = rig.reports[0]
        #expect(first.channel == "kfix")
        #expect(first.item == "kfix-1")
        #expect(first.device == "appletv")
        #expect(first.year == 1998)
        #expect(first.offset == 300)
        #expect(first.seconds == 60)
        #expect(first.runtime == 23 * 60)
        #expect(first.ended == nil)
        #expect(first.session.count == 32)
        rig.clock.advance(60)
        #expect(rig.reports.count == 2)
        // The slot ends 18 minutes in: program_end, then a new session on the same channel.
        rig.clock.advance(16 * 60 + 0.1)
        #expect(rig.reports.last?.ended == .programEnd)
        #expect(rig.reports.last?.session == first.session)
        let ends = rig.reports.filter { $0.ended != nil }.count
        rig.clock.advance(30)
        await rig.press { rig.app.move(.up) }
        #expect(rig.reports.filter { $0.ended != nil }.count == ends + 1)
        #expect(rig.reports.last?.ended == .tuneAway)
        #expect(rig.reports.last?.item == "kfix-2")
    }

    @Test func flippingPastAChannelIsNeverReported() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.clock.advance(4)
        await rig.press { rig.app.move(.up) }
        rig.clock.advance(4)
        await rig.press { rig.app.move(.up) }
        #expect(rig.reports.isEmpty)
    }

    /// The guide's PiP keeps the session going.
    @Test func theGuideKeepsTheSession() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.clock.advance(30)
        rig.app.pressSelect()
        rig.clock.advance(30)
        #expect(rig.reports.count == 1)
        #expect(rig.reports[0].ended == nil)
        #expect(rig.reports[0].seconds == 60)
    }

    // MARK: - Background and long runs

    /// TV-37: background stops playback and sends power_off; coming back refetches the lineup,
    /// retunes at the live position and shows the banner.
    @Test func backgroundAndBack() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.engine.play(duration: 23 * 60)
        rig.clock.advance(120)
        rig.app.enterBackground()
        #expect(rig.reports.last?.ended == .powerOff)
        #expect(rig.engine.calls.last == .stop)
        #expect(rig.app.tuner.slot == nil)
        #expect(rig.clock.waiting.isEmpty)
        let reports = rig.reports.count
        rig.clock.advance(3600)
        #expect(rig.reports.count == reports)
        let lineups = rig.channelRequests
        rig.app.enterForeground()
        await rig.waitForChannels(lineups + 1)
        await rig.server.waitFor(2)
        #expect(rig.channelRequests == lineups + 1)
        #expect(rig.server.requests.count == 2)
        #expect(rig.app.screen == .live)
        #expect(rig.app.bannerVisible)
        rig.answer(now: 70, Make.evening("kfix", from: 69))
        await rig.app.tuner.settled()
        rig.engine.play(duration: 23 * 60)
        #expect(rig.engine.starts.last == 60)
    }

    @Test func foregroundWithoutBackgroundDoesNothing() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.app.enterForeground()
        await settle()
        #expect(rig.server.requests.count == 1)
    }

    @Test func backgroundOnPowerOnDoesNothing() {
        let rig = LiveRig()
        rig.app.enterBackground()
        rig.app.enterForeground()
        #expect(rig.app.screen == .powerOn)
        #expect(rig.reports.isEmpty)
    }

    /// TV-38: at simulated midnight the lineup is fetched again, so a channel launching that day
    /// joins the dial.
    @Test func simulatedMidnightRefetchesTheLineup() async {
        let rig = LiveRig()
        await rig.turnedOn()
        let lineups = rig.channelRequests
        // Midnight is scheduled from the lineup's now, 19:44:29: 4 h 15 min 31 s away.
        rig.clock.advance(4 * 3600 + 15 * 60)
        #expect(rig.channelRequests == lineups)
        rig.clock.advance(40)
        await rig.waitForChannels(lineups + 1)
        #expect(rig.channelRequests == lineups + 1)
    }

    /// TV-39 needs the app to know when it plays.
    @Test func playingIsTrueOnLiveAndInTheGuide() async {
        let rig = LiveRig()
        #expect(!rig.app.isPlaying)
        await rig.turnedOn()
        #expect(rig.app.isPlaying)
        rig.app.pressSelect()
        #expect(rig.app.isPlaying)
        rig.app.enterBackground()
        #expect(!rig.app.isPlaying)
    }

    /// TV-40: the tune that turning on starts is timed from the press.
    @Test func turningOnIsTimedFromThePress() async {
        let rig = LiveRig()
        await rig.turnedOn()
        rig.clock.advance(0.5)
        rig.engine.play(duration: 23 * 60)
        #expect(rig.logs == ["tune kfix 500 ms"])
    }
}
