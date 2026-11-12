import Foundation
import Testing
@testable import RetroCableKit

/// Records each requested delay and returns at once, running a hook first so a test can bring the
/// server back after a number of retries. Real time never passes.
final class RecordingSleeper: Sleeper, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [Double] = []
    private let hook: @Sendable (Int) -> Void

    init(hook: @escaping @Sendable (Int) -> Void = { _ in }) {
        self.hook = hook
    }

    var delays: [Double] {
        lock.withLock { recorded }
    }

    func sleep(seconds: Double) async throws {
        try Task.checkCancellation()
        let count = lock.withLock {
            recorded.append(seconds)
            return recorded.count
        }
        hook(count)
        await Task.yield()
    }
}

@MainActor
@Suite struct AppModelTests {
    let base = URL(string: "https://tv.example.test")!

    func defaults(year: Any? = nil, channel: String? = nil) -> UserDefaults {
        let name = "rc-app-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        if let year { defaults.set(year, forKey: "rc.year") }
        if let channel { defaults.set(channel, forKey: "rc.channel") }
        return defaults
    }

    func model(
        _ transport: StubTransport, _ defaults: UserDefaults, sleeper: RecordingSleeper = RecordingSleeper(),
        clock: ManualClock = ManualClock()
    ) -> AppModel {
        AppModel(
            api: APIClient(baseURL: base, transport: transport), settings: Settings(defaults), sleeper: sleeper,
            clock: clock, timers: clock
        )
    }

    /// TV-3: the power-on text.
    @Test func coldLaunchShowsPowerOn() {
        let app = model(fixtureTransport(), defaults())
        #expect(app.screen == .powerOn)
        #expect(app.placeLine == "Mountain View, CA · 1998")
        #expect(app.serverLine.text == "Tuning…")
        #expect(model(fixtureTransport(), defaults(year: 0)).placeLine == "Mountain View, CA · All Years")
        #expect(model(fixtureTransport(), defaults(year: "2005")).placeLine == "Mountain View, CA · 2005")
        #expect(PowerOnText.button == "Press SELECT to turn on the TV")
        #expect(PowerOnText.wordmark == "RETRO CABLE")
    }

    @Test func serverLineShowsTheVersion() async {
        let app = model(fixtureTransport(), defaults())
        await app.loadVersion()
        #expect(app.serverLine == .version(ServerVersion(version: "0.0.0", commit: "c8e0e3d")))
        #expect(app.serverLine.text == "Server v0.0.0 · c8e0e3d")
    }

    @Test func serverLineSaysNoSignalThenRecovers() async {
        let transport = fixtureTransport(failing: true)
        let sleeper = RecordingSleeper { count in
            if count == 2 { transport.respond { _ in (200, try RepoFiles.fixture("version.json")) } }
        }
        let app = model(transport, defaults(), sleeper: sleeper)
        await app.loadVersion()
        #expect(app.serverLine.text == "Server v0.0.0 · c8e0e3d")
        #expect(sleeper.delays == [5, 5])
        #expect(transport.requests.count == 3)
    }

    @Test func noSignalTextMatchesTheBrowser() {
        #expect(ServerLine.noSignal.text == "No signal")
        #expect(NoSignalText.card == "NO SIGNAL")
        #expect(NoSignalText.message == "Can't reach the cable box server. Retrying…")
    }

    /// TV-4: turning on reaches Live TV on the stored channel.
    @Test func turningOnTunesTheStoredChannel() async {
        let app = model(fixtureTransport(), defaults(channel: "fixture-movies"))
        await app.turnOn()
        #expect(app.screen == .live)
        #expect(app.channelID == "fixture-movies")
        #expect(app.dial.map(\.id) == ["kfix", "retro-classics", "fixture-movies"])
        #expect(app.lineup?.timezone == "America/New_York")
    }

    /// TV-33: off the dial plays the lowest channel and keeps the stored one.
    @Test func storedChannelOffTheDialPlaysTheLowestWithoutForgettingIt() async {
        let store = defaults(channel: "late-toons")
        let app = model(fixtureTransport(), store)
        await app.turnOn()
        #expect(app.channelID == "kfix")
        #expect(Settings(store).channel == "late-toons")
    }

    /// TV-5: no signal, a retry every 5 s, and Live TV once the server answers, with no press.
    @Test func noSignalRetriesEveryFiveSecondsUntilTheServerAnswers() async {
        let transport = fixtureTransport(failing: true)
        let sleeper = RecordingSleeper { count in
            if count == 3 { transport.respond { _ in (200, try RepoFiles.fixture("channels.json")) } }
        }
        let app = model(transport, defaults(), sleeper: sleeper)
        var screens: [AppModel.Screen] = []
        let watcher = Task { @MainActor in
            while !Task.isCancelled {
                if screens.last != app.screen { screens.append(app.screen) }
                await Task.yield()
            }
        }
        await app.turnOn()
        watcher.cancel()
        #expect(sleeper.delays == [5, 5, 5])
        #expect(transport.requests.count == 4)
        #expect(transport.requests.allSatisfy { $0.url?.path == "/api/channels" })
        #expect(app.screen == .live)
        #expect(app.channelID == "kfix")
        #expect(screens.contains(.noSignal))
    }

    /// TV-5 with a host that doesn't answer (powered off): each try hangs until its timeout. The
    /// next try starts 5 s after the last one started, not 5 s after it gave up, so the server is
    /// seen within 5 s of answering again.
    @Test func retriesKeepAFiveSecondCadenceWhenTriesHang() async {
        let clock = ManualClock()
        let hanging = LockedValue(true)
        let transport = StubTransport { request in
            if hanging.value {
                clock.pass(request.timeoutInterval)
                throw URLError(.timedOut)
            }
            return (200, try RepoFiles.fixture("channels.json"))
        }
        let sleeper = RecordingSleeper { count in
            if count == 3 { hanging.value = false }
        }
        let app = model(transport, defaults(), sleeper: sleeper, clock: clock)
        await app.turnOn()
        #expect(app.screen == .live)
        #expect(transport.requests.count == 4)
        // Each hung try already took 4 s, so the next one waits only the 1 s left of the 5.
        #expect(sleeper.delays == [1, 1, 1])
    }

    /// A try that fails at once (connection refused) waits the rest of the 5 s.
    @Test func aQuickFailureWaitsTheRestOfFiveSeconds() async {
        let clock = ManualClock()
        let transport = StubTransport { _ in
            clock.pass(1.5)
            throw URLError(.cannotConnectToHost)
        }
        let sleeper = RecordingSleeper { count in
            if count == 2 { transport.respond { _ in (200, try RepoFiles.fixture("version.json")) } }
        }
        let app = model(transport, defaults(), sleeper: sleeper, clock: clock)
        await app.loadVersion()
        #expect(app.serverLine.text == "Server v0.0.0 · c8e0e3d")
        #expect(sleeper.delays == [3.5, 3.5])
    }

    @Test func noSignalShowsOnTheFirstFailure() async {
        let transport = fixtureTransport(failing: true)
        let sleeper = RecordingSleeper()
        let app = model(transport, defaults(), sleeper: sleeper)
        let task = Task { @MainActor in await app.turnOn() }
        while sleeper.delays.isEmpty { await Task.yield() }
        #expect(app.screen == .noSignal)
        task.cancel()
        await task.value
        #expect(app.screen == .noSignal)
    }

    @Test func appVersionLine() {
        #expect(AppInfo.versionLine(short: "1.0", build: "12") == "App 1.0 (12)")
        #expect(AppInfo.versionLine(short: nil, build: nil) == "App ? (?)")
    }
}

@MainActor
@Suite struct PowerPressTests {
    /// A second press while turning on starts nothing new.
    @Test func pressingPowerTwiceTurnsOnOnce() async {
        let name = "rc-press-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        let transport = fixtureTransport()
        let app = AppModel(
            api: APIClient(baseURL: URL(string: "https://tv.example.test")!, transport: transport),
            settings: Settings(defaults), sleeper: RecordingSleeper()
        )
        app.pressPower()
        app.pressPower()
        await app.turningOn?.value
        #expect(app.screen == .live)
        #expect(transport.requests.filter { $0.url?.path == "/api/channels" }.count == 1)
        app.pressPower()
        #expect(app.turningOn == nil)
    }
}
