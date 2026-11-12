import Foundation

/// "MM-DD" and "HH:MM" of an instant in a zone, for /channels and /guide queries.
public struct ZonedParts: Equatable, Sendable {
    public let date: String
    public let time: String

    public init(date: String, time: String) {
        self.date = date
        self.time = time
    }
}

/// web/src/lib/time.ts. Every time on screen is in the server's zone (`timezone` from the API),
/// never the Apple TV's. Fixed en_US_POSIX patterns, so output never depends on device settings.
public enum TimeFormat {
    static let shortMonths = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    /// "8:14 PM"
    public static func clock(_ t: Date, zone: String) -> String {
        format(t, "h:mm a", zone: zone)
    }

    /// "8:14", the way guide cells label start times.
    public static func shortTime(_ t: Date, zone: String) -> String {
        format(t, "h:mm", zone: zone)
    }

    /// "Saturday, Oct 31, 1998": the clock's date line.
    public static func longDate(_ t: Date, zone: String) -> String {
        format(t, "EEEE, MMM d, yyyy", zone: zone)
    }

    /// "Saturday, October 31, 1998": the year picker's date.
    public static func fullDate(_ t: Date, zone: String) -> String {
        format(t, "EEEE, MMMM d, yyyy", zone: zone)
    }

    /// "Sat 10/31": the guide's day column.
    public static func guideDay(_ t: Date, zone: String) -> String {
        format(t, "EEE M/d", zone: zone)
    }

    /// "Thu, Sep 24, 1998" for an ISO date (yyyy-mm-dd).
    public static func airDate(_ isoDate: String) -> String? {
        guard let day = Timestamp.day(isoDate) else { return nil }
        let noon = Double(Timestamp.daysFromCivil(year: day.year, month: day.month, day: day.day)) * 86_400 + 43_200
        return format(Date(timeIntervalSince1970: noon), "EEE, MMM d, yyyy", zone: "UTC")
    }

    public static func zonedParts(_ t: Date, zone: String) -> ZonedParts {
        ZonedParts(date: format(t, "MM-dd", zone: zone), time: format(t, "HH:mm", zone: zone))
    }

    /// The zone, falling back to the catalog default when the server sends one we don't know.
    public static func timeZone(_ identifier: String) -> TimeZone {
        TimeZone(identifier: identifier) ?? TimeZone(identifier: "America/New_York") ?? .gmt
    }

    private static let cache = FormatterCache()

    private static func format(_ t: Date, _ pattern: String, zone: String) -> String {
        cache.string(t, pattern: pattern, zone: zone)
    }
}

/// DateFormatters are costly to make; keep one per (pattern, zone), behind a lock.
private final class FormatterCache: @unchecked Sendable {
    private let lock = NSLock()
    private var formatters: [String: DateFormatter] = [:]

    func string(_ date: Date, pattern: String, zone: String) -> String {
        lock.withLock {
            let key = "\(pattern)|\(zone)"
            let formatter: DateFormatter
            if let cached = formatters[key] {
                formatter = cached
            } else {
                formatter = DateFormatter()
                formatter.locale = Locale(identifier: "en_US_POSIX")
                formatter.calendar = Calendar(identifier: .gregorian)
                formatter.timeZone = TimeFormat.timeZone(zone)
                formatter.dateFormat = pattern
                formatter.amSymbol = "AM"
                formatter.pmSymbol = "PM"
                formatters[key] = formatter
            }
            return formatter.string(from: date)
        }
    }
}

/// The server's simulated now, carried forward on a monotonic clock: `serverNow` arrived when the
/// monotonic clock read `receivedAt` (seconds).
public struct SimulatedTime: Equatable, Sendable {
    public let serverNow: Date
    public let receivedAt: TimeInterval

    public init(serverNow: Date, receivedAt: TimeInterval) {
        self.serverNow = serverNow
        self.receivedAt = receivedAt
    }

    public func now(at monotonic: TimeInterval) -> Date {
        serverNow.addingTimeInterval(monotonic - receivedAt)
    }
}

/// Seconds on a clock that never jumps (not the wall clock). Injected so tests control time.
public protocol MonotonicClock: Sendable {
    func seconds() -> TimeInterval
}

public struct SystemMonotonicClock: MonotonicClock {
    public init() {}

    public func seconds() -> TimeInterval {
        ProcessInfo.processInfo.systemUptime
    }
}
