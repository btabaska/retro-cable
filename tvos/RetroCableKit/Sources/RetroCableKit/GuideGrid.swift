import Foundation

/// One channel's row: its cells over the whole fetched range (13.5 h from the opening window).
public struct GuideChannel: Equatable, Sendable {
    public let channel: ChannelInfo
    public let cells: [GuideCell]
}

/// The focused cell, as the web keeps it: a row, and the time the focus follows between rows.
public struct GuideFocus: Equatable, Sendable {
    public var row: Int
    public var anchor: Date
}

/// The guide's day column and its three half-hour labels.
public struct GuideTimeBar: Equatable, Sendable {
    public let day: String
    public let labels: [String]
}

/// The guide's grid (web/src/screens/Guide.tsx): a 90-minute window that opens on the current
/// half hour, focus by row and time anchor, and the cable-box moves (TV-22 to TV-25). A value
/// type, so every move is a plain, testable state change.
public struct GuideGrid: Equatable, Sendable {
    /// How far ahead the window may scroll.
    public static let aheadSeconds: TimeInterval = 12 * 3600
    /// One /guide request: the 12 h of scrolling plus the 90-minute window.
    public static let fetchHours: Double = GuideLayout.fetchHours
    /// A guide opened longer ago than this reopens at the current half hour (TV-37).
    public static let staleSeconds: TimeInterval = 30 * 60
    public static let visibleRowCount = TVLayout.Guide.visibleRows
    /// The focused row sits this many rows from the top whenever the list allows.
    public static let focusedRowSlot = TVLayout.Guide.focusedRowSlot

    /// The opening window's start; the window never scrolls before it.
    public let opened: Date
    public private(set) var windowStart: Date
    public private(set) var focus: GuideFocus
    public private(set) var rows: [GuideChannel] = []

    public init(now: Date) {
        opened = GuideLayout.windowStart(for: now)
        windowStart = opened
        focus = GuideFocus(row: 0, anchor: now)
    }

    public var windowEnd: Date { windowStart.addingTimeInterval(GuideLayout.window) }
    public var rangeEnd: Date { opened.addingTimeInterval(Self.fetchHours * 3600) }
    private var latestWindowStart: Date { opened.addingTimeInterval(Self.aheadSeconds) }

    /// The /guide query for this grid: the opening window's date and time in the server's zone.
    public func query(zone: String) -> ZonedParts {
        TimeFormat.zonedParts(opened, zone: zone)
    }

    public func timeBar(zone: String) -> GuideTimeBar {
        GuideTimeBar(
            day: TimeFormat.guideDay(windowStart, zone: zone),
            labels: (0..<3).map {
                TimeFormat.clock(windowStart.addingTimeInterval(Double($0) * GuideLayout.halfHour), zone: zone)
            }
        )
    }

    /// Takes the /guide answer: rows by channel number, focus on the tuned channel's row.
    public mutating func load(_ guide: Guide, tuned: String?) {
        rows = guide.channels
            .sorted { ($0.channel.number, $0.channel.id) < ($1.channel.number, $1.channel.id) }
            .map { GuideChannel(channel: $0.channel, cells: GuideLayout.row($0.slots, from: opened, to: rangeEnd)) }
        focus.row = tunedRow(tuned) ?? 0
    }

    /// The row of a channel, if the guide has it.
    public func tunedRow(_ id: String?) -> Int? {
        guard let id else { return nil }
        return rows.firstIndex { $0.channel.id == id }
    }

    public var focusedRow: GuideChannel? {
        rows.indices.contains(focus.row) ? rows[focus.row] : nil
    }

    public var focusedIndex: Int? {
        guard let row = focusedRow, !row.cells.isEmpty else { return nil }
        return GuideLayout.cellIndex(row.cells, at: focus.anchor)
    }

    public var focusedCell: GuideCell? {
        guard let row = focusedRow, let index = focusedIndex else { return nil }
        return row.cells[index]
    }

    /// The first row on screen: the focused row stays 4th from the top except near the ends.
    public var firstRow: Int {
        min(max(0, focus.row - Self.focusedRowSlot), max(0, rows.count - Self.visibleRowCount))
    }

    public var visibleRows: Range<Int> {
        firstRow..<min(rows.count, firstRow + Self.visibleRowCount)
    }

    /// A row's cells laid out over the current window, for drawing.
    public func windowCells(_ row: Int) -> [GuideCell] {
        guard rows.indices.contains(row) else { return [] }
        return GuideLayout.row(rows[row].cells.map(\.slot), from: windowStart, to: windowEnd)
    }

    public func isFocused(row: Int, _ cell: GuideCell) -> Bool {
        row == focus.row && focusedCell?.start == cell.start
    }

    /// Older than 30 simulated minutes: reopen at the current half hour.
    public func isStale(now: Date) -> Bool {
        now.timeIntervalSince(opened) > Self.staleSeconds
    }

    /// Up/down: one row, keeping the time anchor, stopping at the ends. Left/right: one cell,
    /// scrolling the window by half hours to reveal it, never before the opening window and never
    /// more than 12 h ahead. Left at the earliest cell does nothing.
    public mutating func move(_ direction: MoveDirection) {
        guard !rows.isEmpty else { return }
        switch direction {
        case .up, .down:
            focus.row = max(0, min(rows.count - 1, focus.row + (direction == .up ? -1 : 1)))
        case .left, .right:
            guard let row = focusedRow, let index = focusedIndex else { return }
            let next = index + (direction == .left ? -1 : 1)
            guard row.cells.indices.contains(next) else { return }
            let target = row.cells[next]
            let start = reveal(target)
            guard target.end > start else { return }
            windowStart = start
            focus.anchor = max(target.start, start)
        }
    }

    /// The window start that shows `cell`, moved by half hours within the allowed range.
    private func reveal(_ cell: GuideCell) -> Date {
        var start = windowStart
        while cell.start >= start.addingTimeInterval(GuideLayout.window),
            start.addingTimeInterval(GuideLayout.halfHour) <= latestWindowStart
        {
            start = start.addingTimeInterval(GuideLayout.halfHour)
        }
        while cell.end <= start, start > opened {
            start = start.addingTimeInterval(-GuideLayout.halfHour)
        }
        return start
    }
}

/// What one cell draws (design 5.3, D-8): time and kicker only when full width, the title unless
/// a sliver, the holiday bar, the transplant diamond, past dimming and focus.
public struct GuideCellContent: Equatable, Sendable {
    public let size: CellSize
    /// "7:33" or "from 7:22"; nil unless the cell is full width.
    public let time: String?
    public let kicker: [KickerPart]
    /// Nil for a sliver, or a narrow cell with no room for a letter and the ellipsis; the
    /// accessibility label carries the title.
    public let title: String?
    public let transplant: Bool
    public let holiday: Bool
    /// Ended before simulated now.
    public let past: Bool
    public let focused: Bool
    /// "The Simpsons, 7:33 to 7:56, new, pretend premiere"
    public let accessibilityLabel: String

    public static func make(_ cell: GuideCell, focused: Bool, now: Date, zone: String) -> GuideCellContent {
        let size = GuideLayout.cellSize(cell.width)
        let slot = cell.slot
        let start = TimeFormat.shortTime(cell.start, zone: zone)
        var spoken = [slot.title, "\(start) to \(TimeFormat.shortTime(slot.end, zone: zone))"]
        if slot.isProgram {
            if slot.isNew { spoken.append("new") }
            if slot.kind == .movie { spoken.append("movie") }
            if let holiday = slot.holidayName, !holiday.isEmpty { spoken.append(holiday.lowercased()) }
            if slot.transplant { spoken.append("pretend premiere") }
        }
        return GuideCellContent(
            size: size,
            time: size == .full ? (cell.clippedStart ? "from \(start)" : start) : nil,
            kicker: size == .full ? Program.kicker(slot) : [],
            title: hasTitle(size, cell: cell) ? slot.title : nil,
            transplant: slot.transplant,
            holiday: slot.holiday != nil,
            past: cell.end <= now,
            focused: focused,
            accessibilityLabel: spoken.joined(separator: ", ")
        )
    }
}

extension GuideCellContent {
    /// Slivers never draw a title; narrow cells only when a letter and the ellipsis fit (D-8).
    static func hasTitle(_ size: CellSize, cell: GuideCell) -> Bool {
        switch size {
        case .full: true
        case .narrow: GuideLayout.titleRoom(width: cell.width, transplant: cell.slot.transplant) >= TVLayout.Guide.minTitleRoom
        case .sliver: false
        }
    }
}

/// The guide's details panel for the focused cell (TV-26, design 5.2).
public struct GuideDetails: Equatable, Sendable {
    public static let loading = "Loading the guide…"
    public static let noSignal = "No signal"
    /// "SELECT Watch · ▶❚❚ Change year · BACK Live TV" (D-12).
    public static let hints = [
        RemoteHint(key: "SELECT", action: "Watch"),
        RemoteHint(key: "▶❚❚", action: "Change year"),
        RemoteHint(key: "BACK", action: "Live TV"),
    ]
    public static let pretendPremiereLegend = "Pretend premiere"

    /// "02 · KTVU FOX · 7:33 – 7:56 PM"
    public let line: String
    public let title: String
    /// The episode title; nil when empty.
    public let episode: String?
    /// S5 · E12, NEW, MOVIE, the holiday, PRETEND PREMIERE, in that order.
    public let badges: [BannerContent.Badge]
    /// "Rerun · first aired Thu, Feb 3, 1994", or "No programming scheduled" for filler.
    public let aired: String
    /// The description, else the episode line, else the title; nil for filler.
    public let synopsis: String?

    public static func make(channel: ChannelInfo, cell: GuideCell, zone: String) -> GuideDetails {
        let slot = cell.slot
        var badges: [BannerContent.Badge] = []
        if let season = slot.season, let episode = slot.episode { badges.append(.outline("S\(season) · E\(episode)")) }
        if slot.isNew { badges.append(.new) }
        if slot.kind == .movie { badges.append(.outline("MOVIE")) }
        if let holiday = slot.holidayName, !holiday.isEmpty { badges.append(.holiday(holiday.uppercased())) }
        if slot.transplant { badges.append(.pretendPremiere) }

        let aired: String
        let synopsis: String?
        if slot.kind == .filler {
            aired = "No programming scheduled"
            synopsis = nil
        } else {
            var text = slot.isNew ? "New" : "Rerun"
            if let date = slot.airDate, !date.isEmpty {
                text += " · first aired \(TimeFormat.airDate(date) ?? date)\(slot.transplant ? " (pretend)" : "")"
            }
            aired = text
            let label = Program.label(slot)
            synopsis = [slot.description, label, slot.title].first { !$0.isEmpty } ?? slot.title
        }
        let line = [
            LiveText.number(channel.number),
            channel.callSignAndNetwork,
            "\(TimeFormat.shortTime(cell.start, zone: zone)) – \(TimeFormat.clock(slot.end, zone: zone))",
        ].joined(separator: " · ")
        return GuideDetails(
            line: line,
            title: slot.title,
            episode: slot.episodeTitle.isEmpty ? nil : slot.episodeTitle,
            badges: badges,
            aired: aired,
            synopsis: synopsis
        )
    }
}
