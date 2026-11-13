import Foundation

// Codable models for the TV API (server/openapi.json; docs/specs/api.md). ContractTests keeps
// their keys equal to the schema's properties.

public enum ChannelType: String, Codable, Sendable {
    case network, marathon, movie
}

/// `ChannelInfo`: one channel in a lineup, guide row or tune.
public struct ChannelInfo: Codable, Hashable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var type: ChannelType
    public var number: Int
    public var callSign: String
    public var network: String?
    public var onAir: Bool
    public var library: Bool
    /// "2001-09-02", or nil when the channel was always there.
    public var launch: String?

    enum CodingKeys: String, CodingKey {
        case id, name, type, number, network, library, launch
        case callSign = "call_sign"
        case onAir = "on_air"
    }

    /// "KTVU FOX": the call sign and network, as the banner and details panel show them.
    public var callSignAndNetwork: String {
        [callSign, network].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " ")
    }
}

/// `Lineup` from `GET /api/channels`.
public struct Lineup: Codable, Sendable {
    public var year: Int
    public var date: String
    public var weekday: String
    public var now: Date
    public var timezone: String
    public var channels: [ChannelInfo]
}

public enum SlotKind: String, Codable, Sendable {
    case episode, movie, bumper, filler
}

/// `GuideSlot`: one scheduled item.
public struct GuideSlot: Codable, Hashable, Sendable {
    public var kind: SlotKind
    public var start: Date
    public var end: Date
    public var seconds: Int
    public var title: String
    public var show: String?
    public var episodeTitle: String
    public var season: Int?
    public var episode: Int?
    /// First US air date ("1994-02-03"), or the synthetic premiere for a transplant.
    public var airDate: String?
    public var isNew: Bool
    public var transplant: Bool
    public var holiday: String?
    public var holidayName: String?
    /// Empty when unknown; the contract's default when the key is missing.
    public var description: String

    enum CodingKeys: String, CodingKey {
        case kind, start, end, seconds, title, show, season, episode, transplant, holiday, description
        case episodeTitle = "episode_title"
        case airDate = "air_date"
        case isNew = "new"
        case holidayName = "holiday_name"
    }

    public init(
        kind: SlotKind, start: Date, end: Date, seconds: Int, title: String, show: String?, episodeTitle: String,
        season: Int?, episode: Int?, airDate: String?, isNew: Bool, transplant: Bool, holiday: String?,
        holidayName: String?, description: String
    ) {
        self.kind = kind
        self.start = start
        self.end = end
        self.seconds = seconds
        self.title = title
        self.show = show
        self.episodeTitle = episodeTitle
        self.season = season
        self.episode = episode
        self.airDate = airDate
        self.isNew = isNew
        self.transplant = transplant
        self.holiday = holiday
        self.holidayName = holidayName
        self.description = description
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decode(SlotKind.self, forKey: .kind)
        start = try c.decode(Date.self, forKey: .start)
        end = try c.decode(Date.self, forKey: .end)
        seconds = try c.decode(Int.self, forKey: .seconds)
        title = try c.decode(String.self, forKey: .title)
        show = try c.decodeIfPresent(String.self, forKey: .show)
        episodeTitle = try c.decode(String.self, forKey: .episodeTitle)
        season = try c.decodeIfPresent(Int.self, forKey: .season)
        episode = try c.decodeIfPresent(Int.self, forKey: .episode)
        airDate = try c.decodeIfPresent(String.self, forKey: .airDate)
        isNew = try c.decode(Bool.self, forKey: .isNew)
        transplant = try c.decode(Bool.self, forKey: .transplant)
        holiday = try c.decodeIfPresent(String.self, forKey: .holiday)
        holidayName = try c.decodeIfPresent(String.self, forKey: .holidayName)
        description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
    }

    /// Episodes and movies are programs; bumpers and filler are not.
    public var isProgram: Bool { kind == .episode || kind == .movie }
}

/// A host-relative signed media path ("/media/...?exp=..&sig=.."). It never prints: its
/// description, debug description and mirror hide it, so it can't reach a log by accident (TV-41).
public struct SignedPath: Codable, Hashable, Sendable, CustomStringConvertible, CustomDebugStringConvertible,
    CustomReflectable
{
    let raw: String

    public init(_ raw: String) {
        self.raw = raw
    }

    public init(from decoder: Decoder) throws {
        raw = try decoder.singleValueContainer().decode(String.self)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(raw)
    }

    /// The test stack's demo feeds, which get the browser's title slate. Real media never does.
    public var isDemoFeed: Bool { raw.hasPrefix("/media/_demo/") }

    public var description: String { "<signed media path>" }
    public var debugDescription: String { description }
    public var customMirror: Mirror { Mirror(self, children: [], displayStyle: .struct) }
}

/// `TunedSlot`: a `GuideSlot` plus its media.
@dynamicMemberLookup
public struct TunedSlot: Codable, Hashable, Sendable {
    public var slot: GuideSlot
    /// Nil for filler and for bumpers without media.
    public var mediaPath: SignedPath?

    enum CodingKeys: String, CodingKey {
        case mediaPath = "media_url"
    }

    public init(slot: GuideSlot, mediaPath: SignedPath?) {
        self.slot = slot
        self.mediaPath = mediaPath
    }

    public init(from decoder: Decoder) throws {
        slot = try GuideSlot(from: decoder)
        mediaPath = try decoder.container(keyedBy: CodingKeys.self).decodeIfPresent(SignedPath.self, forKey: .mediaPath)
    }

    public func encode(to encoder: Encoder) throws {
        try slot.encode(to: encoder)
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(mediaPath, forKey: .mediaPath)
    }

    public subscript<T>(dynamicMember keyPath: KeyPath<GuideSlot, T>) -> T {
        slot[keyPath: keyPath]
    }
}

/// `Tune` from `GET /api/tune/{channel}`.
public struct Tune: Codable, Sendable {
    public var channel: ChannelInfo
    public var now: Date
    public var offsetSeconds: Double
    public var current: TunedSlot
    public var next: [TunedSlot]

    enum CodingKeys: String, CodingKey {
        case channel, now, current, next
        case offsetSeconds = "offset_seconds"
    }
}

/// `GuideRow`: one channel's slots in the guide.
public struct GuideRow: Codable, Sendable {
    public var channel: ChannelInfo
    public var slots: [GuideSlot]
}

/// `Guide` from `GET /api/guide`.
public struct Guide: Codable, Sendable {
    public var date: String
    public var weekday: String
    public var timezone: String
    public var start: Date
    public var end: Date
    public var channels: [GuideRow]
}

/// `Version` from `GET /api/version`.
public struct ServerVersion: Codable, Equatable, Sendable {
    public var version: String
    public var commit: String

    public init(version: String, commit: String) {
        self.version = version
        self.commit = commit
    }

    /// "Server v0.0.0 · c8e0e3d"
    public var label: String { "Server v\(version) · \(commit)" }
}

/// `Health` from `GET /api/health`.
public struct Health: Codable, Equatable, Sendable {
    public var status: String
}

public enum WatchKind: String, Codable, Sendable {
    case episode, movie
}

public enum EndReason: String, Codable, Sendable {
    case tuneAway = "tune_away"
    case powerOff = "power_off"
    case programEnd = "program_end"
}

/// `WatchReport` for `POST /api/watch`. Every key is sent, nulls included, as the browser does.
public struct WatchReport: Codable, Equatable, Sendable {
    public var session: String
    public var channel: String
    public var item: String
    public var kind: WatchKind
    public var season: Int?
    public var episode: Int?
    public var year: Int
    public var offset: Int
    public var seconds: Int
    public var runtime: Int
    public var ended: EndReason?
    public var device: String?

    public init(
        session: String, channel: String, item: String, kind: WatchKind, season: Int?, episode: Int?, year: Int,
        offset: Int, seconds: Int, runtime: Int, ended: EndReason?, device: String?
    ) {
        self.session = session
        self.channel = channel
        self.item = item
        self.kind = kind
        self.season = season
        self.episode = episode
        self.year = year
        self.offset = offset
        self.seconds = seconds
        self.runtime = runtime
        self.ended = ended
        self.device = device
    }

    enum CodingKeys: String, CodingKey {
        case session, channel, item, kind, season, episode, year, offset, seconds, runtime, ended, device
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        session = try c.decode(String.self, forKey: .session)
        channel = try c.decode(String.self, forKey: .channel)
        item = try c.decode(String.self, forKey: .item)
        kind = try c.decode(WatchKind.self, forKey: .kind)
        season = try c.decodeIfPresent(Int.self, forKey: .season)
        episode = try c.decodeIfPresent(Int.self, forKey: .episode)
        year = try c.decode(Int.self, forKey: .year)
        offset = try c.decodeIfPresent(Int.self, forKey: .offset) ?? 0
        seconds = try c.decode(Int.self, forKey: .seconds)
        runtime = try c.decode(Int.self, forKey: .runtime)
        ended = try c.decodeIfPresent(EndReason.self, forKey: .ended)
        device = try c.decodeIfPresent(String.self, forKey: .device)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(session, forKey: .session)
        try c.encode(channel, forKey: .channel)
        try c.encode(item, forKey: .item)
        try c.encode(kind, forKey: .kind)
        try c.encode(season, forKey: .season)
        try c.encode(episode, forKey: .episode)
        try c.encode(year, forKey: .year)
        try c.encode(offset, forKey: .offset)
        try c.encode(seconds, forKey: .seconds)
        try c.encode(runtime, forKey: .runtime)
        try c.encode(ended, forKey: .ended)
        try c.encode(device, forKey: .device)
    }
}
