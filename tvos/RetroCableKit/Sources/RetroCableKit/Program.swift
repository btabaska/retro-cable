import Foundation

public struct KickerPart: Equatable, Sendable {
    public enum Tone: Sendable {
        /// Gold, bold.
        case new
        /// Muted.
        case plain
    }

    public let text: String
    public let tone: Tone

    public init(text: String, tone: Tone) {
        self.text = text
        self.tone = tone
    }
}

/// web/src/lib/program.ts
public enum Program {
    /// What follows the start time in a guide cell: NEW · MOVIE. A holiday program is tagged by the
    /// cell's left bar instead; its name is in the details panel and the banner.
    public static func kicker(_ slot: GuideSlot) -> [KickerPart] {
        guard slot.isProgram else { return [] }
        var parts: [KickerPart] = []
        if slot.isNew { parts.append(KickerPart(text: "NEW", tone: .new)) }
        if slot.kind == .movie { parts.append(KickerPart(text: "MOVIE", tone: .plain)) }
        return parts
    }

    /// "Sep 2004" from "2004-09-07".
    public static func monthYear(_ isoDate: String) -> String {
        guard let day = Timestamp.day(String(isoDate.prefix(10))), (1...12).contains(day.month) else { return isoDate }
        return "\(TimeFormat.shortMonths[day.month - 1]) \(day.year)"
    }

    /// 85 minutes -> "1h 25m".
    public static func duration(_ seconds: Int) -> String {
        let minutes = seconds / 60
        let h = minutes / 60
        let m = minutes % 60
        if h == 0 { return "\(m)m" }
        return m == 0 ? "\(h)h" : "\(h)h \(m)m"
    }

    /// The line under a title: "Episode title · S10 E4", or "1998 · 1h 25m" for a movie.
    public static func label(_ slot: GuideSlot) -> String {
        var parts: [String] = []
        if slot.kind == .movie {
            if let airDate = slot.airDate, airDate.count >= 4 { parts.append(String(airDate.prefix(4))) }
            parts.append(duration(slot.seconds))
            return parts.joined(separator: " · ")
        }
        if !slot.episodeTitle.isEmpty { parts.append(slot.episodeTitle) }
        if let season = slot.season, let episode = slot.episode { parts.append("S\(season) E\(episode)") }
        return parts.joined(separator: " · ")
    }
}
