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
        /// The grid with the live PiP.
        case guide
        /// Opened from the guide; the picture is hidden and silent while it shows.
        case yearPicker
    }

    /// The guide's /guide request: "Loading the guide…", the grid, or "No signal" (retried).
    public enum GuideStatus: Equatable, Sendable {
        case loading
        case loaded
        case failed
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

    /// The guide's grid while the guide (or the year picker over it) shows; nil on Live TV, so
    /// opening the guide always starts at the current half hour.
    public private(set) var guide: GuideGrid?
    public private(set) var guideStatus: GuideStatus = .loading
    /// The year picker while it shows.
    public private(set) var yearPicker: YearPickerModel?
    /// The year change the picker started, if any.
    public private(set) var changingYear: Task<Void, Never>?

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
    private var guideRequest: Task<Void, Never>?
    private var guideRetry: TimerHandle?

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
        case .guide, .yearPicker: false
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

    /// Swipe or click. Live TV: up/down change channel, left/right show the banner. The guide and
    /// the year picker move their focus.
    public func move(_ direction: MoveDirection) {
        switch screen {
        case .live:
            switch direction {
            case .up: step(1)
            case .down: step(-1)
            case .left, .right: showBanner()
            }
        case .guide: guide?.move(direction)
        case .yearPicker: yearPicker?.move(direction)
        case .powerOn, .noSignal: break
        }
    }

    /// Select (a click, not a hold). Live TV opens the guide; the guide tunes the focused row's
    /// channel; the year picker sets the year.
    public func pressSelect() {
        switch screen {
        case .powerOn: pressPower()
        case .live: openGuide()
        case .guide: watchFocusedChannel()
        case .yearPicker: pickYear()
        case .noSignal: break
        }
    }

    /// Play/Pause. Power on turns on; Live TV goes to the last channel; the guide opens the year
    /// picker.
    public func pressPlayPause() {
        switch screen {
        case .powerOn: pressPower()
        case .live: lastChannel()
        case .guide: openYearPicker()
        case .yearPicker, .noSignal: break
        }
    }

    /// Back, when the app handles it (`backLeavesApp` is false): Live TV shows the banner, the
    /// guide returns to Live TV without a retune, the year picker returns to the guide.
    public func pressBack() {
        switch screen {
        case .live: if !bannerVisible { showBanner() }
        case .guide: closeGuide()
        case .yearPicker: closeYearPicker()
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

    // MARK: - Guide

    /// Opens the guide at the current half hour, focused on the tuned channel's program now. The
    /// picture keeps playing in the PiP (TV-27).
    public func openGuide() {
        guard screen == .live else { return }
        screen = .guide
        startGuide()
        updateWatching()
    }

    /// Back to Live TV, same channel, no retune.
    public func closeGuide() {
        guard screen == .guide else { return }
        screen = .live
        dropGuide()
        updateWatching()
    }

    /// The details panel for the focused cell; nil while loading or after a failure.
    public func guideDetails() -> GuideDetails? {
        guard let guide, let row = guide.focusedRow, let cell = guide.focusedCell else { return nil }
        return GuideDetails.make(channel: row.channel, cell: cell, zone: zone)
    }

    /// Select in the guide: tune the focused row's channel to what airs now, back to Live TV with
    /// the banner (TV-28). Nothing before the guide has loaded.
    private func watchFocusedChannel() {
        guard let id = guide?.focusedRow?.channel.id else { return }
        closeGuide()
        tune(to: id)
        showBanner()
    }

    private func startGuide(now: Date? = nil) {
        guide = GuideGrid(now: now ?? simNow())
        guideStatus = .loading
        loadGuide()
    }

    /// One /guide request for 13.5 h from the window start; a failure reads "No signal" and is
    /// retried every 5 s while the guide shows (TV-22).
    private func loadGuide() {
        guard let grid = guide else { return }
        stopGuideLoad()
        let query = grid.query(zone: zone)
        let asked = year
        let opened = grid.opened
        guideRequest = Task { [weak self, api] in
            let answer = try? await api.guide(
                year: asked, date: query.date, start: query.time, hours: GuideGrid.fetchHours
            )
            guard let self, !Task.isCancelled, self.year == asked, self.guide?.opened == opened else { return }
            self.guideRequest = nil
            if let answer {
                self.guide?.load(answer, tuned: self.channelID)
                self.guideStatus = .loaded
            } else {
                self.guideStatus = .failed
                guard self.screen == .guide, !self.backgrounded else { return }
                self.guideRetry = self.timers.schedule(after: Self.retrySeconds) { [weak self] in
                    self?.guideRetry = nil
                    self?.loadGuide()
                }
            }
        }
    }

    private func stopGuideLoad() {
        guideRequest?.cancel()
        guideRequest = nil
        guideRetry?.cancel()
        guideRetry = nil
    }

    private func dropGuide() {
        stopGuideLoad()
        guide = nil
        guideStatus = .loading
    }

    // MARK: - Year picker

    /// Play/Pause in the guide: the year picker, focused on the current year. The picture stops
    /// (hidden and silent), which ends the watch session with tune_away (TV-31, TV-35).
    private func openYearPicker() {
        guard screen == .guide else { return }
        screen = .yearPicker
        stopGuideLoad()
        let picker = YearPickerModel(current: year, timers: timers) { [api] in try await api.channels(year: $0) }
        yearPicker = picker
        picker.start()
        tuner.stop()
    }

    /// Back: the guide as it was, the channel again at its live position, nothing changed.
    private func closeYearPicker() {
        guard screen == .yearPicker else { return }
        yearPicker?.close()
        yearPicker = nil
        screen = .guide
        if guideStatus != .loaded { loadGuide() }
        retune()
    }

    /// Select: set the focused year, back to Live TV with a retune and the banner.
    private func pickYear() {
        guard let picker = yearPicker else { return }
        let picked = picker.focused
        changingYear = Task { [weak self] in
            await self?.changeYear(picked)
            self?.changingYear = nil
        }
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
        yearPicker?.close()
        yearPicker = nil
        dropGuide()
        if screen == .guide || screen == .yearPicker { screen = .live }
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
        stopGuideLoad()
        yearPicker?.close()
    }

    /// Back on screen: fetch the lineup again (the simulated date may have changed) and retune at
    /// the live position, keeping the screen; Live TV shows the banner, and a stale guide reopens
    /// at the current half hour (TV-37).
    public func enterForeground() {
        guard backgrounded else { return }
        backgrounded = false
        guard screen == .live || screen == .guide || screen == .yearPicker else { return }
        switch screen {
        case .live:
            showBanner()
        case .guide:
            // A guide opened more than 30 simulated minutes ago reopens at the current half hour.
            if let guide, guide.isStale(now: simNow()) {
                startGuide()
            } else if guideStatus != .loaded {
                loadGuide()
            }
        case .yearPicker:
            yearPicker?.start()
        case .powerOn, .noSignal:
            break
        }
        scheduleHeartbeat()
        refreshing = Task { [weak self] in
            guard let self else { return }
            await self.refreshLineup()
            guard !self.backgrounded else { return }
            if self.midnightTimer == nil { self.scheduleMidnight() }
            // The monotonic clock may have stopped while the Apple TV slept: check the guide
            // again against the server's now from the fresh lineup.
            let now = self.latestNow()
            if self.screen == .guide, let guide = self.guide, guide.isStale(now: now) { self.startGuide(now: now) }
            // The year picker keeps the picture off; leaving it retunes.
            if self.screen != .yearPicker { self.retune() }
        }
    }

    /// Simulated now from whichever server answer (/tune or /channels) arrived last.
    private func latestNow() -> Date {
        let times = [tuner.simTime, lineupTime].compactMap { $0 }
        guard let latest = times.max(by: { $0.receivedAt < $1.receivedAt }) else { return simNow() }
        return latest.now(at: clock.seconds())
    }

    /// Fetch this year's lineup again, keeping the channel when it is still on the dial.
    private func refreshLineup() async {
        let asked = year
        guard let lineup = try? await api.channels(year: asked), !Task.isCancelled, asked == year else { return }
        let before = channelID
        apply(lineup, keepChannel: true)
        if channelID != before, !backgrounded, screen != .yearPicker { retune() }
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
