/// web/src/lib/years.ts
public enum Years {
    public static let first = 1990
    public static let last = 2010
    /// The year the server takes for All Years: every channel, every episode, today's real date.
    public static let all = 0
    /// What a missing or invalid stored year means.
    public static let defaultYear = 1998
    /// The picker's order: 1990-2010, then All Years on its own row.
    public static let pickable: [Int] = Array(first...last) + [all]

    /// "1998", or "All Years".
    public static func label(_ year: Int) -> String {
        year == all ? "All Years" : String(year)
    }

    /// A stored year, or nil when there is none or it isn't one the server takes.
    public static func parse(_ raw: String?) -> Int? {
        guard let raw, !raw.isEmpty, raw.allSatisfy(\.isASCII), raw.allSatisfy(\.isNumber), let year = Int(raw)
        else { return nil }
        return valid(year)
    }

    public static func valid(_ year: Int) -> Int? {
        year == all || (first...last).contains(year) ? year : nil
    }
}

/// web/src/lib/channels.ts
public enum Channels {
    /// Channel up/down over the dial (ids by channel number), wrapping at the ends.
    public static func step(_ dial: [String], from current: String, by delta: Int) -> String {
        guard !dial.isEmpty else { return current }
        guard let index = dial.firstIndex(of: current) else { return dial[0] }
        let count = dial.count
        return dial[((index + delta) % count + count) % count]
    }

    /// The dial: channels on the air, by number. Off-air channels are never tuned.
    public static func dial(_ channels: [ChannelInfo]) -> [ChannelInfo] {
        channels.filter(\.onAir).sorted { ($0.number, $0.id) < ($1.number, $1.id) }
    }

    /// The channel to play: the stored one if it is on the dial, else the lowest-numbered.
    public static func starting(_ dial: [String], stored: String?) -> String? {
        if let stored, dial.contains(stored) { return stored }
        return dial.first
    }
}

/// web/src/lib/focus.ts
public struct GridPoint: Equatable, Hashable, Sendable {
    public var row: Int
    public var col: Int

    public init(row: Int, col: Int) {
        self.row = row
        self.col = col
    }
}

public struct GridSize: Equatable, Sendable {
    public var rows: Int
    public var cols: Int

    public init(rows: Int, cols: Int) {
        self.rows = rows
        self.cols = cols
    }
}

/// A Siri Remote swipe or click direction.
public enum MoveDirection: Sendable {
    case up, down, left, right
}

public enum Focus {
    /// The next focused cell, clamped at the edges like a cable box.
    public static func move(_ from: GridPoint, _ direction: MoveDirection, in size: GridSize) -> GridPoint {
        let (dr, dc): (Int, Int) = switch direction {
        case .up: (-1, 0)
        case .down: (1, 0)
        case .left: (0, -1)
        case .right: (0, 1)
        }
        return GridPoint(
            row: max(0, min(size.rows - 1, from.row + dr)),
            col: max(0, min(size.cols - 1, from.col + dc))
        )
    }
}
