import CoreGraphics
import Foundation

/// One program's cell in a guide row.
public struct GuideCell: Equatable, Sendable {
    public let slot: GuideSlot
    public let start: Date
    /// Where the next program starts, so the bumper between them belongs to this cell.
    public let end: Date
    /// Fraction of the window from its left edge.
    public let left: Double
    public let width: Double
    public let clippedStart: Bool
}

public enum CellSize: Sendable {
    /// Time, kicker and title.
    case full
    /// The ellipsised title only.
    case narrow
    /// No text; the accessibility label carries the title.
    case sliver
}

/// web/src/lib/guide.ts, measured against tvOS's 1494 pt cell area.
public enum GuideLayout {
    /// The guide shows 90 minutes.
    public static let window: TimeInterval = 90 * 60
    public static let halfHour: TimeInterval = 30 * 60
    /// One /guide request: 12 h of scrolling plus the 90-minute window.
    public static let fetchHours: Double = 13.5
    public static let narrowPoints: CGFloat = 120
    public static let sliverPoints: CGFloat = 40

    /// Cells for one channel row: programs at their true length, bumpers folded in.
    public static func row(_ slots: [GuideSlot], from windowStart: Date, to windowEnd: Date) -> [GuideCell] {
        let programs = slots.filter { $0.kind != .bumper }
        let span = windowEnd.timeIntervalSince(windowStart)
        guard span > 0 else { return [] }
        var cells: [GuideCell] = []
        for (i, slot) in programs.enumerated() {
            let start = slot.start
            let end = i + 1 < programs.count ? programs[i + 1].start : slot.end
            if end <= windowStart || start >= windowEnd { continue }
            let from = max(start, windowStart)
            let to = min(end, windowEnd)
            cells.append(GuideCell(
                slot: slot,
                start: start,
                end: end,
                left: from.timeIntervalSince(windowStart) / span,
                width: to.timeIntervalSince(from) / span,
                clippedStart: start < windowStart
            ))
        }
        return cells
    }

    /// The cell airing at `t`, or the nearest one when none covers it.
    public static func cellIndex(_ cells: [GuideCell], at t: Date) -> Int {
        if let hit = cells.firstIndex(where: { $0.start <= t && t < $0.end }) { return hit }
        guard let first = cells.first, t >= first.start else { return 0 }
        return cells.count - 1
    }

    /// Guide windows start on the half hour.
    public static func windowStart(for t: Date) -> Date {
        Date(timeIntervalSince1970: (t.timeIntervalSince1970 / halfHour).rounded(.down) * halfHour)
    }

    /// How much text a cell of `width` (a fraction of the window) has room for.
    public static func cellSize(_ width: Double, cellsWidth: CGFloat = TVLayout.Guide.cellsWidth) -> CellSize {
        let points = CGFloat(width) * cellsWidth
        if points < sliverPoints { return .sliver }
        if points < narrowPoints { return .narrow }
        return .full
    }
}
