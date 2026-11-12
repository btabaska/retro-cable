import Foundation

/// Small pieces of Live TV text.
public enum LiveText {
    public static let tuning = "Tuning…"

    /// Channel numbers are always at least two digits: "02".
    public static func number(_ number: Int) -> String {
        number < 10 && number >= 0 ? "0\(number)" : String(number)
    }

    /// "02 KTVU": the bumper slate and demo slate heading.
    public static func numberAndCallSign(_ channel: ChannelInfo) -> String {
        "\(number(channel.number)) \(channel.callSign)"
    }

    /// TV-9: "The Simpsons S5E12: file runs 1381.0 s but its slot is 1376 s (+5.0 s)", or nil
    /// within `tolerance`. Title, episode and lengths only; never the URL.
    public static func lengthMismatch(_ slot: GuideSlot, fileSeconds: Double, tolerance: Double) -> String? {
        let diff = fileSeconds - Double(slot.seconds)
        guard abs(diff) > tolerance else { return nil }
        var name = slot.title
        if let season = slot.season, let episode = slot.episode { name += " S\(season)E\(episode)" }
        let sign = diff > 0 ? "+" : ""
        return "\(name): file runs \(String(format: "%.1f", fileSeconds)) s but its slot is \(slot.seconds) s "
            + "(\(sign)\(String(format: "%.1f", diff)) s)"
    }
}

/// A remote control legend: the key word in bold, then what it does.
public struct RemoteHint: Equatable, Sendable {
    public let key: String
    public let action: String

    public init(key: String, action: String) {
        self.key = key
        self.action = action
    }
}

/// The clock box: "7:34 PM" over "Wednesday, Nov 11, 1998", from simulated time in the server's
/// zone, never the Apple TV's own date (TV-21).
public struct ClockText: Equatable, Sendable {
    public let time: String
    public let date: String

    public static func make(_ now: Date, zone: String) -> ClockText {
        ClockText(time: TimeFormat.clock(now, zone: zone), date: TimeFormat.longDate(now, zone: zone))
    }
}

/// Everything the info banner says (web/src/components/InfoBanner.tsx; TV-19, TV-20).
public struct BannerContent: Equatable, Sendable {
    public enum Badge: Equatable, Sendable {
        case new
        /// The holiday's name, upper case.
        case holiday(String)
        case pretendPremiere
        /// Outlined in `textMuted`: "S5 · E12" and "MOVIE" (the guide's details panel).
        case outline(String)

        public var text: String {
            switch self {
            case .new: "NEW"
            case .holiday(let name): name
            case .pretendPremiere: "PRETEND PREMIERE"
            case .outline(let text): text
            }
        }
    }

    /// "Next 7:56" (bold) and the program's title.
    public struct NextLine: Equatable, Sendable {
        public let time: String
        public let title: String
    }

    /// "02"
    public let number: String
    /// "KTVU FOX"
    public let callSign: String
    public let title: String
    /// "Bart Gets Famous · S5 E12", "1998 · 1h 25m"; nil for bumpers, filler and when empty.
    public let episode: String?
    public let badges: [Badge]
    /// Nil when empty, and always for bumpers and filler: no line, no gap.
    public let description: String?
    /// "7:33"; nil before the slot is known.
    public let start: String?
    public let end: String?
    /// Elapsed share of the slot on simulated time, 0...1.
    public let progress: Double
    /// The next program, skipping bumpers.
    public let next: NextLine?
    /// "Year 1998" or "All Years".
    public let year: String

    /// "▲▼ Channel  SELECT Guide  ▶❚❚ Last channel" (D-12: Siri Remote controls only).
    public static let hints = [
        RemoteHint(key: "▲▼", action: "Channel"),
        RemoteHint(key: "SELECT", action: "Guide"),
        RemoteHint(key: "▶❚❚", action: "Last channel"),
    ]

    /// `next` is the next program already skipping bumpers (`Tuner.nextProgram`); a bumper passed
    /// in is skipped too.
    public static func make(
        channel: ChannelInfo, slot: GuideSlot?, next: GuideSlot?, now: Date, year: Int, zone: String
    ) -> BannerContent {
        let program = slot.map { $0.isProgram } ?? false
        var badges: [Badge] = []
        if let slot, program {
            if slot.isNew { badges.append(.new) }
            if let holiday = slot.holidayName, !holiday.isEmpty { badges.append(.holiday(holiday.uppercased())) }
            if slot.transplant { badges.append(.pretendPremiere) }
        }
        let title: String = switch slot?.kind {
        case nil: LiveText.tuning
        case .bumper?: channel.name
        default: slot?.title ?? LiveText.tuning
        }
        let episode = slot.flatMap { program ? Program.label($0) : nil }.flatMap { $0.isEmpty ? nil : $0 }
        let description = slot.flatMap { program && !$0.description.isEmpty ? $0.description : nil }
        var progress = 0.0
        if let slot {
            let length = slot.end.timeIntervalSince(slot.start)
            if length > 0 { progress = min(1, max(0, now.timeIntervalSince(slot.start) / length)) }
        }
        let upNext = next.flatMap { $0.kind == .bumper ? nil : $0 }
        return BannerContent(
            number: LiveText.number(channel.number),
            callSign: channel.callSignAndNetwork,
            title: title,
            episode: episode,
            badges: badges,
            description: description,
            start: slot.map { TimeFormat.shortTime($0.start, zone: zone) },
            end: slot.map { TimeFormat.shortTime($0.end, zone: zone) },
            progress: progress,
            next: upNext.map { NextLine(time: "Next \(TimeFormat.shortTime($0.start, zone: zone))", title: $0.title) },
            year: year == Years.all ? Years.label(year) : "Year \(year)"
        )
    }
}
