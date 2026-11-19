import CoreGraphics
import Foundation
import Observation

/// The year grid: 1990-2010 in three rows of seven, All Years alone on a fourth row (TV-29).
public enum YearGrid {
    public static let columns = TVLayout.YearPicker.columns
    public static let rows: [[Int]] = stride(from: 0, to: Years.pickable.count, by: columns).map {
        Array(Years.pickable[$0..<min($0 + columns, Years.pickable.count)])
    }

    /// Where a year sits; an unknown year counts as the first cell.
    public static func position(_ year: Int) -> GridPoint {
        let index = Years.pickable.firstIndex(of: year) ?? 0
        return GridPoint(row: index / columns, col: index % columns)
    }

    /// Arrows clamp at the edges. Down from the third row reaches All Years; up from it returns
    /// to the third row's first year (web/src/screens/YearPicker.tsx).
    public static func move(_ year: Int, _ direction: MoveDirection) -> Int {
        let next = Focus.move(position(year), direction, in: GridSize(rows: rows.count, cols: columns))
        return Years.pickable[min(Years.pickable.count - 1, next.row * columns + next.col)]
    }
}

/// One channel on the year picker's dial: "02", its name and its note (TV-30).
public struct DialEntry: Equatable, Sendable, Identifiable {
    public let id: String
    public let number: String
    public let name: String
    public let note: String
    /// Off-air channels are dimmed.
    public let onAir: Bool

    public init(_ channel: ChannelInfo) {
        id = channel.id
        number = LiveText.number(channel.number)
        name = channel.name
        onAir = channel.onAir
        note = if !channel.onAir {
            "Hidden until \(channel.launch.map { String($0.prefix(4)) } ?? "later")"
        } else if channel.type == .movie {
            "Your playlist"
        } else if channel.library {
            "Always on (classics)"
        } else {
            "On air"
        }
    }
}

/// The picker's left panel and dial title for the focused year (TV-30).
public struct YearPanel: Equatable, Sendable {
    public static let copy = "Each channel plays what had aired by this date, new episodes and reruns mixed."
    public static let allYearsCopy =
        "All Years: every channel on, airing every season of every show, whenever it first aired."

    public let kicker = "Today on your TV is"
    /// "Wednesday, November 11, 1998"; the year label while the lineup loads.
    public let date: String
    public let sub: String
    /// "On the dial in 1998"
    public let dialTitle: String
    /// Every channel by number; nil while loading.
    public let dial: [DialEntry]?

    public static func make(year: Int, lineup: Lineup?) -> YearPanel {
        YearPanel(
            date: lineup.map(longDate) ?? Years.label(year),
            sub: year == Years.all ? allYearsCopy : copy,
            dialTitle: "On the dial in \(Years.label(year))",
            dial: lineup.map { $0.channels.sorted { ($0.number, $0.id) < ($1.number, $1.id) }.map(DialEntry.init) }
        )
    }

    /// "Wednesday, November 11, 1998" from the lineup's date and weekday.
    static func longDate(_ lineup: Lineup) -> String {
        guard let day = Timestamp.day(lineup.date), (1...12).contains(day.month) else { return lineup.date }
        return "\(lineup.weekday), \(TimeFormat.months[day.month - 1]) \(day.day), \(day.year)"
    }
}

/// The dial in two columns (design 6): 12 rows of 56 pt each fit 24 channels; more channels
/// shrink the rows to 44 pt, and beyond that the last row reads "+N more".
public struct DialColumns: Equatable, Sendable {
    public let columns: [[DialEntry]]
    public let rowHeight: CGFloat
    /// Channels that didn't fit; the last row says how many.
    public let more: Int

    public var moreText: String? { more > 0 ? "+\(more) more" : nil }

    public static func make(_ entries: [DialEntry], height: CGFloat = TVLayout.YearPicker.dialListHeight) -> DialColumns {
        typealias Y = TVLayout.YearPicker
        let most = Int(height / Y.dialRowMinHeight)
        let perColumn = min(most, max(Y.dialRows, (entries.count + 1) / 2))
        let fitted = (height / CGFloat(perColumn) * 2).rounded(.down) / 2
        let rowHeight = max(Y.dialRowMinHeight, min(Y.dialRowHeight, fitted))
        // Past two full columns, the last row gives way to "+N more".
        let shown = entries.count > 2 * perColumn ? 2 * perColumn - 1 : entries.count
        let first = Array(entries.prefix(min(shown, perColumn)))
        let second = Array(entries.prefix(shown).dropFirst(perColumn))
        return DialColumns(columns: [first, second], rowHeight: rowHeight, more: entries.count - shown)
    }
}

/// The year picker's state: focus, the lineups fetched while it is open, and the fetch timing:
/// 120 ms after focus lands on a year, retried after 1 s, 2 s ... up to 5 s (TV-30).
@MainActor
@Observable
public final class YearPickerModel {
    public static let fetchDelay: Double = 0.12
    public static let retryStep: Double = 1
    public static let maxRetry: Double = 5

    /// The year playing now: ringed, apart from focus.
    public let current: Int
    public private(set) var focused: Int
    public private(set) var lineups: [Int: Lineup] = [:]

    @ObservationIgnored private let timers: any TimerScheduler
    @ObservationIgnored private let fetch: @Sendable (Int) async throws -> Lineup
    @ObservationIgnored private var failures = 0
    @ObservationIgnored private var timer: TimerHandle?
    @ObservationIgnored private var request: Task<Void, Never>?
    @ObservationIgnored private var open = false

    public init(current: Int, timers: any TimerScheduler, fetch: @escaping @Sendable (Int) async throws -> Lineup) {
        self.current = current
        focused = Years.valid(current) ?? Years.defaultYear
        self.timers = timers
        self.fetch = fetch
    }

    public var panel: YearPanel { YearPanel.make(year: focused, lineup: lineups[focused]) }

    public func start() {
        open = true
        schedule()
    }

    public func move(_ direction: MoveDirection) {
        let next = YearGrid.move(focused, direction)
        guard next != focused else { return }
        focused = next
        failures = 0
        schedule()
    }

    /// Stops every timer and request.
    public func close() {
        open = false
        cancel()
    }

    private func cancel() {
        timer?.cancel()
        timer = nil
        request?.cancel()
        request = nil
    }

    private func schedule() {
        cancel()
        guard open, lineups[focused] == nil else { return }
        let year = focused
        let delay = failures == 0 ? Self.fetchDelay : min(Self.retryStep * Double(failures), Self.maxRetry)
        timer = timers.schedule(after: delay) { [weak self] in
            guard let self else { return }
            self.timer = nil
            self.request = Task { [weak self, fetch] in
                let result: Lineup?
                do { result = try await fetch(year) } catch { result = nil }
                guard let self, !Task.isCancelled, self.open else { return }
                self.request = nil
                if let result {
                    self.lineups[year] = result
                    self.failures = 0
                } else if self.focused == year {
                    self.failures += 1
                    self.schedule()
                }
            }
        }
    }
}
