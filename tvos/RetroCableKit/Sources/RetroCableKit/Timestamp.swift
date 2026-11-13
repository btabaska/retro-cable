import Foundation

/// A calendar day ("1994-02-03") with no time or zone.
public struct CalendarDay: Equatable, Hashable, Sendable {
    public let year: Int
    public let month: Int
    public let day: Int

    public init(year: Int, month: Int, day: Int) {
        self.year = year
        self.month = month
        self.day = day
    }
}

/// Parses the server's ISO 8601 timestamps ("1998-11-11T19:44:29.090069-05:00") exactly,
/// microseconds and all, without a formatter or the device's zone.
public enum Timestamp {
    public static func parse(_ text: String) -> Date? {
        let bytes = Array(text.utf8)
        guard bytes.count >= 20, bytes[4] == 0x2D, bytes[7] == 0x2D, bytes[10] == 0x54 || bytes[10] == 0x20,
              bytes[13] == 0x3A, bytes[16] == 0x3A,
              let year = number(bytes, 0, 4), let month = number(bytes, 5, 2), let day = number(bytes, 8, 2),
              let hour = number(bytes, 11, 2), let minute = number(bytes, 14, 2), let second = number(bytes, 17, 2),
              (1...12).contains(month), (1...31).contains(day), hour < 24, minute < 60, second < 61
        else { return nil }
        var index = 19
        var fraction = 0.0
        if index < bytes.count, bytes[index] == 0x2E {
            index += 1
            var scale = 0.1
            let begin = index
            while index < bytes.count, let digit = digit(bytes[index]) {
                fraction += Double(digit) * scale
                scale /= 10
                index += 1
            }
            if index == begin { return nil }
        }
        guard index < bytes.count else { return nil }
        var offset = 0
        switch bytes[index] {
        case 0x5A, 0x7A: // Z
            guard index + 1 == bytes.count else { return nil }
        case 0x2B, 0x2D: // + or -
            guard bytes.count == index + 6, bytes[index + 3] == 0x3A,
                  let oh = number(bytes, index + 1, 2), let om = number(bytes, index + 4, 2), oh < 24, om < 60
            else { return nil }
            offset = (oh * 3600 + om * 60) * (bytes[index] == 0x2D ? -1 : 1)
        default:
            return nil
        }
        let days = daysFromCivil(year: year, month: month, day: day)
        let whole = days * 86_400 + hour * 3600 + minute * 60 + second - offset
        return Date(timeIntervalSince1970: Double(whole) + fraction)
    }

    /// "1994-02-03" -> its parts, or nil.
    public static func day(_ text: String) -> CalendarDay? {
        let bytes = Array(text.utf8)
        guard bytes.count == 10, bytes[4] == 0x2D, bytes[7] == 0x2D,
              let year = number(bytes, 0, 4), let month = number(bytes, 5, 2), let day = number(bytes, 8, 2),
              (1...12).contains(month), (1...31).contains(day)
        else { return nil }
        return CalendarDay(year: year, month: month, day: day)
    }

    private static func digit(_ byte: UInt8) -> Int? {
        (0x30...0x39).contains(byte) ? Int(byte - 0x30) : nil
    }

    private static func number(_ bytes: [UInt8], _ start: Int, _ length: Int) -> Int? {
        guard start + length <= bytes.count else { return nil }
        var value = 0
        for i in start..<(start + length) {
            guard let d = digit(bytes[i]) else { return nil }
            value = value * 10 + d
        }
        return value
    }

    /// Days since 1970-01-01 in the proleptic Gregorian calendar (Howard Hinnant's algorithm).
    static func daysFromCivil(year: Int, month: Int, day: Int) -> Int {
        let y = month <= 2 ? year - 1 : year
        let era = (y >= 0 ? y : y - 399) / 400
        let yoe = y - era * 400
        let mp = (month + 9) % 12
        let doy = (153 * mp + 2) / 5 + day - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        return era * 146_097 + doe - 719_468
    }
}
