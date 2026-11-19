import Foundation

/// What is on screen: the slot playing on a channel in a year, and the simulated now.
public struct Watching: Equatable, Sendable {
    public var channel: String
    public var year: Int
    public var slotStart: Date
    public var seconds: Int
    public var kind: SlotKind
    public var show: String?
    public var season: Int?
    public var episode: Int?
    /// Simulated now, to work out how far into the program the viewer tuned in.
    public var simNow: Date

    public init(
        channel: String, year: Int, slotStart: Date, seconds: Int, kind: SlotKind, show: String?, season: Int?,
        episode: Int?, simNow: Date
    ) {
        self.channel = channel
        self.year = year
        self.slotStart = slotStart
        self.seconds = seconds
        self.kind = kind
        self.show = show
        self.season = season
        self.episode = episode
        self.simNow = simNow
    }

    public init(channel: String, year: Int, slot: GuideSlot, simNow: Date) {
        self.init(
            channel: channel, year: year, slotStart: slot.start, seconds: slot.seconds, kind: slot.kind,
            show: slot.show, season: slot.season, episode: slot.episode, simNow: simNow
        )
    }
}

/// Viewing reports for the weekly station manager (web/src/lib/watch.ts): one session per program
/// watched on a channel, full screen or in the guide's PiP. Fire and forget: sending never blocks
/// or slows tuning, failures are silent, and nothing about a report is logged.
@MainActor
public final class WatchReporter {
    /// Less than this is flipping past a channel, not watching it: never reported.
    public static let minSeconds = 10
    /// How often an open session is reported while it plays.
    public static let heartbeatSeconds: TimeInterval = 60
    public static let device = "appletv"

    private struct Open {
        let key: String
        let channel: String
        let year: Int
        /// The real (monotonic) clock when it began playing.
        let since: TimeInterval
        let report: WatchReport
        var ended = false
    }

    private var open: Open?
    private let send: (WatchReport) -> Void
    private let now: () -> TimeInterval
    private let makeID: () -> String

    public init(
        send: @escaping (WatchReport) -> Void,
        now: @escaping () -> TimeInterval,
        makeID: @escaping () -> String = WatchReporter.randomSessionID
    ) {
        self.send = send
        self.now = now
        self.makeID = makeID
    }

    /// A random 32-character hex id.
    public nonisolated static func randomSessionID() -> String {
        UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    }

    /// Posts in a detached task and drops any failure: never waits, never throws, never logs.
    public nonisolated static func poster(_ api: APIClient) -> (WatchReport) -> Void {
        { report in
            Task.detached(priority: .utility) {
                try? await api.postWatch(report)
            }
        }
    }

    private static func key(_ w: Watching) -> String {
        "\(w.channel)|\(w.year)|\(w.slotStart.timeIntervalSince1970)"
    }

    /// Call whenever what is playing may have changed; nil when nothing is being watched.
    public func watch(_ next: Watching?) {
        let program = next.flatMap { w in (w.kind == .episode || w.kind == .movie) && w.show != nil ? w : nil }
        if let open, let program, open.key == Self.key(program) { return }
        if let open {
            let sameChannel = next.map { $0.channel == open.channel && $0.year == open.year } ?? false
            end(sameChannel ? .programEnd : .tuneAway)
        }
        guard let program, let show = program.show else { return }
        let offset = max(0, Int((program.simNow.timeIntervalSince(program.slotStart)).rounded()))
        open = Open(
            key: Self.key(program),
            channel: program.channel,
            year: program.year,
            since: now(),
            report: WatchReport(
                session: makeID(), channel: program.channel, item: show,
                kind: program.kind == .movie ? .movie : .episode, season: program.season, episode: program.episode,
                year: program.year, offset: min(offset, program.seconds), seconds: 0,
                runtime: max(1, program.seconds), ended: nil, device: Self.device
            )
        )
    }

    /// The periodic report while a program plays.
    public func heartbeat() {
        report(nil)
    }

    /// The program stopped being watched.
    public func end(_ reason: EndReason) {
        report(reason)
        open = nil
    }

    private func report(_ ended: EndReason?) {
        guard var current = open, !current.ended else { return }
        let seconds = min(current.report.runtime, Int((now() - current.since).rounded(.down)))
        guard seconds >= Self.minSeconds else { return }
        if ended != nil {
            current.ended = true
            open = current
        }
        var body = current.report
        body.seconds = seconds
        body.ended = ended
        send(body)
    }
}
