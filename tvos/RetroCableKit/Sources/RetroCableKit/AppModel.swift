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

/// The app's top-level state: which screen shows, the lineup and the channel. Views read it and
/// send it remote presses; it does the fetching and retrying.
@MainActor
@Observable
public final class AppModel {
    public enum Screen: Equatable, Sendable {
        case powerOn
        case noSignal
        case live
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

    /// The turn-on in flight, if any.
    public private(set) var turningOn: Task<Void, Never>?

    public let api: APIClient
    private let settings: Settings
    private let sleeper: any Sleeper

    public init(api: APIClient, settings: Settings, sleeper: any Sleeper = TaskSleeper()) {
        self.api = api
        self.settings = settings
        self.sleeper = sleeper
    }

    public var year: Int { settings.year }

    /// "Mountain View, CA · 1998" ("All Years" for 0).
    public var placeLine: String { "\(PowerOnText.place) · \(Years.label(year))" }

    public var channel: ChannelInfo? { dial.first { $0.id == channelID } }

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
        turningOn = Task { [weak self] in
            await self?.turnOn()
            self?.turningOn = nil
        }
    }

    /// Turning on (and the no-signal loop): fetch the lineup and go to Live
    /// TV. Until a lineup arrives the screen is no signal, retried every 5 s with no press needed.
    public func turnOn() async {
        while !Task.isCancelled {
            do {
                let lineup = try await api.channels(year: year)
                apply(lineup)
                screen = .live
                return
            } catch is CancellationError {
                return
            } catch {
                if lineup == nil { screen = .noSignal }
            }
            do { try await sleeper.sleep(seconds: Self.retrySeconds) } catch { return }
        }
    }

    private func apply(_ lineup: Lineup) {
        self.lineup = lineup
        dial = Channels.dial(lineup.channels)
        channelID = Channels.starting(dial.map(\.id), stored: settings.channel)
    }
}
