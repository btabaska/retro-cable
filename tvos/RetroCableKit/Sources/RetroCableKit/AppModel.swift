import Foundation
import Observation

/// Waits between retries. Real time in the app; a recorder in tests, so no test ever sleeps.
public protocol Sleeper: Sendable {
    func sleep(seconds: Double) async throws
}

public struct TaskSleeper: Sleeper {
    public init() {}

    public func sleep(seconds: Double) async throws {
        try await Task.sleep(for: .seconds(seconds))
    }
}

/// The power-on screen's server line.
public enum ServerLine: Equatable, Sendable {
    case tuning
    case version(ServerVersion)
    case noSignal

    public var text: String {
        switch self {
        case .tuning: "Tuning…"
        case .version(let version): version.label
        case .noSignal: "No signal"
        }
    }
}

public enum PowerOnText {
    public static let wordmark = "RETRO CABLE"
    public static let place = "Mountain View, CA"
    public static let button = "Press SELECT to turn on the TV"
}

public enum NoSignalText {
    public static let card = "NO SIGNAL"
    public static let message = "Can't reach the cable box server. Retrying…"
}

public enum AppInfo {
    /// "App 1.0 (12)", so Brandon can tell which sideload is installed.
    public static func versionLine(short: String?, build: String?) -> String {
        "App \(short ?? "?") (\(build ?? "?"))"
    }

    public static func versionLine(bundle: Bundle = .main) -> String {
        versionLine(
            short: bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
            build: bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        )
    }
}

/// The app's top-level state: which screen shows, the lineup, the channel and the tuner. Views
/// read it and send it remote presses; it does the fetching, retrying, timing and reporting.
@MainActor
@Observable
public final class AppModel {
    public enum Screen: Equatable, Sendable {
        case powerOn
        case noSignal
        case live
        /// Milestone 3 fills it in; for now it shows the PiP and returns on Back.
        case guide
    }

    /// How long no signal waits between tries, and how long the server line waits after a failure.
    public static let retrySeconds: Double = 5

    public private(set) var screen: Screen = .powerOn
    public private(set) var serverLine: ServerLine = .tuning
    public private(set) var lineup: Lineup?
    /// The lineup's on-air channels by number.
    public private(set) var dial: [ChannelInfo] = []
    /// The channel playing (or about to).
    public private(set) var channelID: String?
    /// The channel tuned before this one in this session: Play/Pause goes back to it.
    public private(set) var lastChannelID: String?
    /// 1990-2010, or 0 for All Years.
    public private(set) var year: Int
    /// The info banner and clock box (TV-18): 4 s of real time after each trigger.
    public private(set) var bannerVisible = false
    /// The app is in the background: nothing plays, nothing is scheduled.
    public private(set) var backgrounded = false

    /// The turn-on in flight, if any.
    public private(set) var turningOn: Task<Void, Never>?

    public let api: APIClient
    public let tuner: Tuner
    private let settings: Settings
    private let sleeper: any Sleeper
    private let clock: any MonotonicClock
    private let timers: any TimerScheduler
    private let reporter: WatchReporter
    /// The lineup's simulated now, for the clock before the first tune answers.
    private var lineupTime: SimulatedTime?
    private var bannerTimer: TimerHandle?
    private var heartbeatTimer: TimerHandle?
    private var midnightTimer: TimerHandle?
    private var refreshing: Task<Void, Never>?
    private var powerPressedAt: TimeInterval?

    /// - Parameters:
    ///   - engine: the one video player (AVPlayer in the app).
    ///   - clock, timers: the real clock; tests pass a manual one.
    ///   - tuneFetch: /tune; defaults to `api`.
    ///   - sendWatch: where watch reports go; defaults to posting them to `api`, fire and forget.
    ///   - log: debug lines (tune times, length mismatches). Never URLs.
    public init(
        api: APIClient,
        settings: Settings,
        sleeper: any Sleeper = TaskSleeper(),
        engine: (any PlaybackEngine)? = nil,
        clock: any MonotonicClock = SystemMonotonicClock(),
        timers: any TimerScheduler = TaskTimerScheduler(),
        tuneFetch: (@Sendable (String, Int) async throws -> Tune)? = nil,
        sendWatch: ((WatchReport) -> Void)? = nil,
        log: @escaping @MainActor (String) -> Void = { _ in }
    ) {
        self.api = api
        self.settings = settings
        self.sleeper = sleeper
        self.clock = clock
        self.timers = timers
        year = settings.year
        tuner = Tuner(
            fetch: tuneFetch ?? { [api] channel, year in try await api.tune(channel: channel, year: year) },
            engine: engine ?? SilentEngine(),
            clock: clock,
            timers: timers,
            mediaURL: { [api] in api.mediaURL($0) },
            log: log
        )
        reporter = WatchReporter(send: sendWatch ?? WatchReporter.poster(api), now: { [clock] in clock.seconds() })
        tuner.onSlotChange = { [weak self] in self?.updateWatching() }
    }

    /// "Mountain View, CA · 1998" ("All Years" for 0).
    public var placeLine: String { "\(PowerOnText.place) · \(Years.label(year))" }

    public var channel: ChannelInfo? { dial.first { $0.id == channelID } }

    /// The server's zone; every time on screen is in it.
    public var zone: String { lineup?.timezone ?? "America/New_York" }

    /// Live TV or the guide's PiP is playing: the screensaver stays off (TV-39).
    public var isPlaying: Bool { (screen == .live || screen == .guide) && !backgrounded }

    /// Back on Live TV leaves the app when the banner shows (TV-17); elsewhere the screens decide.
    public var backLeavesApp: Bool {
        switch screen {
        case .powerOn, .noSignal: true
        case .live: bannerVisible
        case .guide: false
        }
    }

    /// Simulated now: the latest /tune answer's, else the lineup's, carried on the monotonic clock.
    public func simNow() -> Date {
        tuner.simNow() ?? lineupTime?.now(at: clock.seconds()) ?? lineup?.now ?? Date(timeIntervalSince1970: 0)
    }

    /// What the banner says now; nil before a channel is known.
    public func bannerContent() -> BannerContent? {
        guard let channel = tuner.channel ?? channel else { return nil }
        return BannerContent.make(
            channel: channel, slot: tuner.slot?.slot, next: tuner.nextProgram?.slot, now: simNow(), year: year,
            zone: zone
        )
    }

    public func clockText() -> ClockText {
        ClockText.make(simNow(), zone: zone)
    }

    // MARK: - Power on and no signal

    /// Fills the server line, retrying every 5 s until the server answers or the task is cancelled.
    public func loadVersion() async {
        while !Task.isCancelled {
            do {
                serverLine = .version(try await api.version())
                return
            } catch is CancellationError {
                return
            } catch {
                serverLine = .noSignal
            }
            do { try await sleeper.sleep(seconds: Self.retrySeconds) } catch { return }
        }
    }

    /// Select or Play/Pause on power on. Ignored anywhere else, and while a turn-on is running.
    public func pressPower() {
        guard screen == .powerOn, turningOn == nil else { return }
        powerPressedAt = clock.seconds()
        turningOn = Task { [weak self] in
            await self?.turnOn()
            self?.turningOn = nil
        }
    }

    /// Turning on (and the no-signal loop): fetch the lineup and go to Live TV on the stored
    /// channel. Until a lineup arrives the screen is no signal, retried every 5 s with no press.
    public func turnOn() async {
        while !Task.isCancelled {
            do {
                let lineup = try await api.channels(year: year)
                apply(lineup, keepChannel: false)
                screen = .live
                startLive(pressedAt: powerPressedAt)
                return
            } catch is CancellationError {
                return
            } catch {
                if lineup == nil { screen = .noSignal }
            }
            do { try await sleeper.sleep(seconds: Self.retrySeconds) } catch { return }
        }
    }

    private func startLive(pressedAt: TimeInterval?) {
        powerPressedAt = nil
        retune(pressedAt: pressedAt)
        showBanner()
        scheduleHeartbeat()
    }

    private func apply(_ lineup: Lineup, keepChannel: Bool) {
        self.lineup = lineup
        lineupTime = SimulatedTime(serverNow: lineup.now, receivedAt: clock.seconds())
        tuner.zone = lineup.timezone
        dial = Channels.dial(lineup.channels)
        let ids = dial.map(\.id)
        if !keepChannel || !(channelID.map(ids.contains) ?? false) {
            // Off the dial falls back to the lowest channel without forgetting the stored one (TV-33).
            channelID = Channels.starting(ids, stored: settings.channel)
        }
        scheduleMidnight()
    }

    /// Tune the current channel again from the live position.
    private func retune(pressedAt: TimeInterval? = nil) {
        guard let channel else {
            tuner.stop()
            return
        }
        tuner.tune(channel, year: year, pressedAt: pressedAt ?? clock.seconds())
    }

    // MARK: - Remote

    /// Swipe or click. Live TV: up/down change channel, left/right show the banner.
    public func move(_ direction: MoveDirection) {
        guard screen == .live else { return }
        switch direction {
        case .up: step(1)
        case .down: step(-1)
        case .left, .right: showBanner()
        }
    }

    /// Select (a click, not a hold). Live TV opens the guide.
    public func pressSelect() {
        switch screen {
        case .powerOn: pressPower()
        case .live: openGuide()
        case .guide: closeGuide()
        case .noSignal: break
        }
    }

    /// Play/Pause. Power on turns on; Live TV goes to the last channel.
    public func pressPlayPause() {
        switch screen {
        case .powerOn: pressPower()
        case .live: lastChannel()
        case .guide, .noSignal: break
        }
    }

    /// Back, when the app handles it (`backLeavesApp` is false): Live TV shows the banner, the
    /// guide returns to Live TV without a retune.
    public func pressBack() {
        switch screen {
        case .live: if !bannerVisible { showBanner() }
        case .guide: closeGuide()
        case .powerOn, .noSignal: break
        }
    }

    /// Channel up (+1) or down (-1) over the dial, wrapping (TV-13).
    public func step(_ delta: Int) {
        guard let current = channelID else { return }
        tune(to: Channels.step(dial.map(\.id), from: current, by: delta))
    }

    /// The previous channel, if it is on this dial (TV-15).
    public func lastChannel() {
        guard let last = lastChannelID, dial.contains(where: { $0.id == last }) else { return }
        tune(to: last)
    }

    /// Tune a channel on the dial on purpose: it becomes the stored channel, the one before it
    /// becomes last channel, and the banner shows. The tune starts on the press (TV-40).
    public func tune(to id: String) {
        guard let target = dial.first(where: { $0.id == id }) else { return }
        if id != channelID || tuner.channel?.id != id {
            if let current = channelID, current != id { lastChannelID = current }
            channelID = id
            settings.channel = id
            tuner.tune(target, year: year, pressedAt: clock.seconds())
        }
        showBanner()
    }

    public func openGuide() {
        guard screen == .live else { return }
        screen = .guide
        updateWatching()
    }

    public func closeGuide() {
        guard screen == .guide else { return }
        screen = .live
        updateWatching()
    }

    /// Show the banner and clock for 4 s of real time, restarting the 4 s (TV-18).
    public func showBanner() {
        bannerVisible = true
        bannerTimer?.cancel()
        bannerTimer = timers.schedule(after: TVLayout.Banner.holdSeconds) { [weak self] in
            self?.bannerVisible = false
            self?.bannerTimer = nil
        }
    }

    // MARK: - Year

    /// Set and save the year, fetch its lineup and retune: the stored channel if it is on the new
    /// dial, else the lowest (TV-31, TV-33). The banner shows.
    public func changeYear(_ newYear: Int) async {
        guard let valid = Years.valid(newYear) else { return }
        let pressedAt = clock.seconds()
        settings.year = valid
        year = valid
        tuner.stop()
        if screen == .guide { screen = .live }
        showBanner()
        while !Task.isCancelled {
            do {
                let lineup = try await api.channels(year: valid)
                guard year == valid else { return }
                apply(lineup, keepChannel: false)
                retune(pressedAt: pressedAt)
                showBanner()
                return
            } catch is CancellationError {
                return
            } catch {
                do { try await sleeper.sleep(seconds: Self.retrySeconds) } catch { return }
            }
        }
    }

    // MARK: - Watch reporting

    private func updateWatching() {
        guard isPlaying, let channel = tuner.channel, let slot = tuner.slot else {
            reporter.watch(nil)
            return
        }
        reporter.watch(Watching(channel: channel.id, year: tuner.year, slot: slot.slot, simNow: simNow()))
    }

    private func scheduleHeartbeat() {
        heartbeatTimer?.cancel()
        heartbeatTimer = timers.schedule(after: WatchReporter.heartbeatSeconds) { [weak self] in
            self?.reporter.heartbeat()
            self?.scheduleHeartbeat()
        }
    }

    // MARK: - Background and long runs

    /// The app left the screen (TV button, Home, another app): stop playing, end the session with
    /// power_off and stop every timer (TV-37).
    public func enterBackground() {
        guard screen != .powerOn, !backgrounded else { return }
        backgrounded = true
        reporter.end(.powerOff)
        tuner.stop()
        for timer in [bannerTimer, heartbeatTimer, midnightTimer] { timer?.cancel() }
        bannerTimer = nil
        heartbeatTimer = nil
        midnightTimer = nil
        bannerVisible = false
        refreshing?.cancel()
    }

    /// Back on screen: fetch the lineup again (the simulated date may have changed) and retune at
    /// the live position, keeping the screen; Live TV shows the banner (TV-37).
    public func enterForeground() {
        guard backgrounded else { return }
        backgrounded = false
        guard screen == .live || screen == .guide else { return }
        if screen == .live { showBanner() }
        scheduleHeartbeat()
        refreshing = Task { [weak self] in
            guard let self else { return }
            await self.refreshLineup()
            guard !self.backgrounded else { return }
            if self.midnightTimer == nil { self.scheduleMidnight() }
            self.retune()
        }
    }

    /// Fetch this year's lineup again, keeping the channel when it is still on the dial.
    private func refreshLineup() async {
        let asked = year
        guard let lineup = try? await api.channels(year: asked), !Task.isCancelled, asked == year else { return }
        let before = channelID
        apply(lineup, keepChannel: true)
        if channelID != before, !backgrounded { retune() }
    }

    /// TV-38: at simulated midnight in the server's zone, fetch the lineup again so a channel
    /// launching that day joins the dial.
    private func scheduleMidnight() {
        midnightTimer?.cancel()
        midnightTimer = nil
        guard !backgrounded, screen != .powerOn || lineup != nil else { return }
        let now = simNow()
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeFormat.timeZone(zone)
        guard let midnight = calendar.nextDate(
            after: now, matching: DateComponents(hour: 0, minute: 0, second: 0), matchingPolicy: .nextTime
        ) else { return }
        midnightTimer = timers.schedule(after: midnight.timeIntervalSince(now) + 1) { [weak self] in
            guard let self else { return }
            self.midnightTimer = nil
            self.refreshing = Task { [weak self] in await self?.refreshLineup() }
        }
    }
}
