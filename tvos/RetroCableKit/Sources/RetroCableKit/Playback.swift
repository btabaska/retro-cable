import Foundation

/// What the player tells the tuner. `id` is the load it belongs to, so a late event from a file
/// the tuner has moved past is ignored.
public enum PlaybackEvent: Equatable, Sendable {
    /// The file is ready to play; `duration` is its real length in seconds (nil if unknown).
    case ready(id: Int, duration: Double?)
    /// The first frame of this load is on screen.
    case firstFrame(id: Int)
    /// The file failed to load or to keep playing (refused, expired signature, unplayable).
    case failed(id: Int)
    /// The file played to its end.
    case ended(id: Int)
}

/// The one video player the app keeps for its whole life (AVPlayer in the app, a fake in tests).
/// The tuner drives it; it never decides anything itself.
@MainActor
public protocol PlaybackEngine: AnyObject {
    var onEvent: (@MainActor (PlaybackEvent) -> Void)? { get set }
    /// Replace whatever plays (its picture and sound stop at once) and prepare `url`. Never log `url`.
    func load(_ url: URL, id: Int)
    /// Seek load `id` to `seconds` and play.
    func start(id: Int, at seconds: Double)
    /// Stop now: no picture, no sound.
    func stop()
    /// Seconds left in the file while it plays, else nil.
    func secondsLeft() -> Double?
}

/// A player that does nothing, for models built without one.
@MainActor
public final class SilentEngine: PlaybackEngine {
    public var onEvent: (@MainActor (PlaybackEvent) -> Void)?

    public init() {}

    public func load(_: URL, id _: Int) {}
    public func start(id _: Int, at _: Double) {}
    public func stop() {}
    public func secondsLeft() -> Double? { nil }
}

/// The station ID slate for a bumper without media: "02 KTVU", the name, "Up next 8:27 Seinfeld".
public struct BumperSlate: Equatable, Sendable {
    public let callSign: String
    public let name: String
    public let upNext: String?

    public init(callSign: String, name: String, upNext: String?) {
        self.callSign = callSign
        self.name = name
        self.upNext = upNext
    }

    public static func make(channel: ChannelInfo, next: GuideSlot?, zone: String) -> BumperSlate {
        BumperSlate(
            callSign: LiveText.numberAndCallSign(channel),
            name: channel.name,
            upNext: next.map { "Up next \(TimeFormat.shortTime($0.start, zone: zone)) \($0.title)" }
        )
    }
}

/// The browser's title slate over demo feeds (the test stack). Real media never gets it.
public struct DemoSlate: Equatable, Sendable {
    public let title: String
    public let detail: String

    public init(title: String, detail: String) {
        self.title = title
        self.detail = detail
    }

    public static func make(_ slot: TunedSlot, channel: ChannelInfo, next: GuideSlot?) -> DemoSlate? {
        guard let path = slot.mediaPath, path.isDemoFeed else { return nil }
        if slot.kind == .bumper {
            return DemoSlate(
                title: LiveText.numberAndCallSign(channel),
                detail: next.map { "Up next: \($0.title)" } ?? "Stay tuned"
            )
        }
        let detail: String = if let season = slot.season, let episode = slot.episode {
            "S\(season) · E\(episode) — \(slot.episodeTitle)"
        } else if slot.kind == .movie {
            "Feature presentation"
        } else {
            ""
        }
        return DemoSlate(title: slot.title, detail: detail)
    }
}

/// What the player area shows.
public enum Picture: Equatable, Sendable {
    /// Tuning or loading: a digital box cuts to black, then to picture.
    case black
    /// Still no picture `Tuner.stallSeconds` after a tune, or /tune failed: color bars titled
    /// "Tuning…" (as the browser shows), so a long outage never looks like a hang (TV-12).
    case tuning
    /// The file is playing.
    case video
    /// A bumper without media.
    case bumperSlate(BumperSlate)
    /// Filler, or a file that failed twice: color bars and the title.
    case filler(title: String)
}

/// One tune's press-to-first-frame time (TV-40). Channel id and milliseconds only.
public struct TuneLatency: Equatable, Sendable {
    public let channel: String
    public let milliseconds: Int

    public init(channel: String, milliseconds: Int) {
        self.channel = channel
        self.milliseconds = milliseconds
    }

    /// "tune ktvu 850 ms"
    public var logLine: String { "tune \(channel) \(milliseconds) ms" }
}

/// The last 50 tune times, for reading the p95 off the debug log.
public struct TuneLatencyLog: Equatable, Sendable {
    public static let capacity = 50
    public private(set) var entries: [TuneLatency] = []

    public init() {}

    public mutating func record(_ entry: TuneLatency) {
        entries.append(entry)
        if entries.count > Self.capacity { entries.removeFirst(entries.count - Self.capacity) }
    }

    /// The 95th percentile (nearest rank), or nil with no entries.
    public var p95: Int? {
        guard !entries.isEmpty else { return nil }
        let sorted = entries.map(\.milliseconds).sorted()
        let rank = Int((0.95 * Double(sorted.count)).rounded(.up))
        return sorted[max(0, rank - 1)]
    }
}
