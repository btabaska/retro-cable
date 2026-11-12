import Foundation
@testable import RetroCableKit

/// A monotonic clock and timer scheduler that only moves when a test says so. `advance` fires
/// every due timer in time order, setting the clock to each one's due time first.
final class ManualClock: MonotonicClock, TimerScheduler, @unchecked Sendable {
    private struct Pending {
        let id: Int
        let due: TimeInterval
        let action: @MainActor () -> Void
    }

    private let lock = NSLock()
    private var current: TimeInterval
    private var pending: [Pending] = []
    private var nextID = 0

    init(start: TimeInterval = 1_000) {
        current = start
    }

    func seconds() -> TimeInterval {
        lock.withLock { current }
    }

    /// Delays of the timers still waiting, from now, soonest first.
    var waiting: [TimeInterval] {
        lock.withLock { pending.map { $0.due - current }.sorted() }
    }

    @MainActor
    func schedule(after seconds: Double, _ action: @escaping @MainActor () -> Void) -> TimerHandle {
        let id = lock.withLock {
            nextID += 1
            pending.append(Pending(id: nextID, due: current + max(0, seconds), action: action))
            return nextID
        }
        return TimerHandle { [weak self] in
            guard let self else { return }
            self.lock.withLock { self.pending.removeAll { $0.id == id } }
        }
    }

    /// Moves time forward, firing due timers (and any they schedule that also fall due).
    @MainActor
    func advance(_ seconds: TimeInterval) {
        let target = lock.withLock { current + seconds }
        while true {
            let next: Pending? = lock.withLock {
                guard let soonest = pending.filter({ $0.due <= target }).min(by: { ($0.due, $0.id) < ($1.due, $1.id) })
                else { return nil }
                pending.removeAll { $0.id == soonest.id }
                current = max(current, soonest.due)
                return soonest
            }
            guard let next else { break }
            next.action()
        }
        lock.withLock { current = target }
    }
}

/// Records what the tuner asks of the player; the test sends the player's events back.
@MainActor
final class FakeEngine: PlaybackEngine {
    enum Call: Equatable {
        case load(id: Int, path: String)
        case start(id: Int, at: Double)
        case stop
    }

    var onEvent: (@MainActor (PlaybackEvent) -> Void)?
    var calls: [Call] = []
    var left: Double?
    var muted = false

    var lastLoadID: Int? {
        calls.reversed().compactMap { call -> Int? in
            if case .load(let id, _) = call { return id }
            return nil
        }.first
    }

    var loads: [String] {
        calls.compactMap { call -> String? in
            if case .load(_, let path) = call { return path }
            return nil
        }
    }

    var starts: [Double] {
        calls.compactMap { call -> Double? in
            if case .start(_, let at) = call { return at }
            return nil
        }
    }

    func load(_ url: URL, id: Int) {
        calls.append(.load(id: id, path: url.path))
    }

    func start(id: Int, at seconds: Double) {
        calls.append(.start(id: id, at: seconds))
    }

    func stop() {
        calls.append(.stop)
    }

    func secondsLeft() -> Double? {
        left
    }

    func send(_ event: PlaybackEvent) {
        onEvent?(event)
    }

    /// The loaded file is ready, then shows its first frame.
    func play(duration: Double?) {
        guard let id = lastLoadID else { return }
        send(.ready(id: id, duration: duration))
        send(.firstFrame(id: id))
    }
}

/// A /tune server whose answers the test hands out one by one, in any order.
@MainActor
final class TuneServer {
    struct Request: Equatable {
        let channel: String
        let year: Int
    }

    private(set) var requests: [Request] = []
    private var waiting: [Int: CheckedContinuation<Tune, Error>] = [:]

    var fetch: @Sendable (String, Int) async throws -> Tune {
        { [self] channel, year in try await self.request(channel, year) }
    }

    private func request(_ channel: String, _ year: Int) async throws -> Tune {
        let index = requests.count
        requests.append(Request(channel: channel, year: year))
        return try await withCheckedThrowingContinuation { waiting[index] = $0 }
    }

    var open: [Int] { waiting.keys.sorted() }

    /// Lets the tuner's request tasks run until `count` requests have arrived (or gives up).
    func waitFor(_ count: Int) async {
        for _ in 0..<10_000 where requests.count < count {
            await Task.yield()
        }
    }

    func answer(_ index: Int, _ tune: Tune) {
        waiting.removeValue(forKey: index)?.resume(returning: tune)
    }

    func fail(_ index: Int) {
        waiting.removeValue(forKey: index)?.resume(throwing: APIError.unreachable)
    }

    /// Answers the latest request.
    func answerLast(_ tune: Tune) {
        answer(requests.count - 1, tune)
    }

    func failLast() {
        fail(requests.count - 1)
    }
}

/// Builds channels, slots and tunes for the live tests. Made-up titles only.
enum Make {
    static let zone = "America/New_York"
    /// 7:30 PM Wednesday Nov 11 1998 in New York.
    static let base = Timestamp.parse("1998-11-11T19:30:00-05:00")!

    static func channel(_ id: String, number: Int, callSign: String? = nil, network: String? = nil, onAir: Bool = true)
        -> ChannelInfo
    {
        ChannelInfo(
            id: id, name: "\(id.uppercased()) Channel", type: .network, number: number,
            callSign: callSign ?? id.uppercased(), network: network, onAir: onAir, library: false, launch: nil
        )
    }

    static func slot(
        _ title: String, at minute: Double, seconds: Int, kind: SlotKind = .episode, media: String? = "auto",
        show: String? = nil, season: Int? = nil, episode: Int? = nil, episodeTitle: String = "",
        isNew: Bool = false, transplant: Bool = false, holidayName: String? = nil, description: String = "",
        airDate: String? = nil
    ) -> TunedSlot {
        let start = base.addingTimeInterval(minute * 60)
        let path: SignedPath? = switch media {
        case nil: nil
        case "auto": SignedPath("/media/tv/\(title.lowercased().replacingOccurrences(of: " ", with: "-")).mp4?exp=0&sig=not-a-real-signature")
        case let raw?: SignedPath(raw)
        }
        let isProgram = kind == .episode || kind == .movie
        return TunedSlot(
            slot: GuideSlot(
                kind: kind, start: start, end: start.addingTimeInterval(Double(seconds)), seconds: seconds, title: title,
                show: show ?? (isProgram ? title.lowercased().replacingOccurrences(of: " ", with: "-") : nil),
                episodeTitle: episodeTitle, season: season, episode: episode, airDate: airDate, isNew: isNew,
                transplant: transplant, holiday: holidayName?.lowercased(), holidayName: holidayName,
                description: description
            ),
            mediaPath: path
        )
    }

    /// A /tune answer: `now` is `minute` minutes past 7:30 PM.
    static func tune(_ channel: ChannelInfo, now minute: Double, _ slots: [TunedSlot]) -> Tune {
        let now = base.addingTimeInterval(minute * 60)
        return Tune(
            channel: channel, now: now, offsetSeconds: now.timeIntervalSince(slots[0].start),
            current: slots[0], next: Array(slots.dropFirst())
        )
    }

    /// Four half-hour-ish episodes from 7:30 PM, back to back.
    static func evening(_ prefix: String = "Show", from minute: Double = 0, count: Int = 4) -> [TunedSlot] {
        (0..<count).map { i in slot("\(prefix) \(i + 1)", at: minute + Double(i) * 23, seconds: 23 * 60) }
    }
}

/// Lets queued main-actor work run (answers resuming, tasks finishing).
@MainActor
func settle(_ rounds: Int = 30) async {
    for _ in 0..<rounds { await Task.yield() }
}

/// What the rig's /api/guide answers: by default the fixture dial's on-air channels, each running
/// half-hour shows ("<id> 1", "<id> 2", ...) from 7:30 PM for the whole 13.5 h. Thread-safe, as
/// the transport calls it off the main actor.
final class GuideFeed: @unchecked Sendable {
    private let lock = NSLock()
    private var failingNow = false

    var failing: Bool {
        get { lock.withLock { failingNow } }
        set { lock.withLock { failingNow = newValue } }
    }

    func answer() throws -> (Int, Data) {
        if failing { throw URLError(.cannotConnectToHost) }
        return (200, try Self.body())
    }

    static func body() throws -> Data {
        let lineup = try APIClient.decoder.decode(Lineup.self, from: RepoFiles.fixture("channels.json"))
        let rows = lineup.channels.filter(\.onAir).map { channel in
            GuideRow(channel: channel, slots: (0..<27).map { i in
                Make.slot("\(channel.id) \(i + 1)", at: Double(i) * 30, seconds: 30 * 60).slot
            })
        }
        let guide = Guide(
            date: "1998-11-11", weekday: "Wednesday", timezone: Make.zone, start: Make.base,
            end: Make.base.addingTimeInterval(13.5 * 3600), channels: rows
        )
        let encoder = JSONEncoder()
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(identifier: Make.zone)
        encoder.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(formatter.string(from: date))
        }
        return try encoder.encode(guide)
    }
}

/// Lets main-actor work run until `condition` holds (or gives up).
@MainActor
func until(_ condition: () -> Bool) async {
    for _ in 0..<10_000 where !condition() {
        await Task.yield()
    }
}

/// A value behind a lock, for test switches the transport reads off the main actor.
final class LockedValue<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: T

    init(_ value: T) {
        stored = value
    }

    var value: T {
        get { lock.withLock { stored } }
        set { lock.withLock { stored = newValue } }
    }
}
