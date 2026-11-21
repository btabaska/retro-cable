import Foundation
import Testing
@testable import RetroCableKit

@Suite struct TimestampTests {
    @Test func parsesOffsetsAndFractions() throws {
        let plain = try #require(Timestamp.parse("1998-11-11T19:33:15-05:00"))
        #expect(plain.timeIntervalSince1970 == 910_830_795)
        // The server sends microseconds on `now`; keep them.
        let micro = try #require(Timestamp.parse("1998-11-11T19:33:15.090069-05:00"))
        #expect(abs(micro.timeIntervalSince1970 - 910_830_795.090069) < 0.000_001)
        let zulu = try #require(Timestamp.parse("1998-11-12T00:33:15Z"))
        #expect(zulu == plain)
        let east = try #require(Timestamp.parse("1998-11-12T05:33:15+05:00"))
        #expect(east == plain)
    }

    @Test func crossesDSTByOffsetNotZone() throws {
        // 1:30 AM happens twice on Oct 25 1998 in New York; the offset tells them apart.
        let edt = try #require(Timestamp.parse("1998-10-25T01:30:00-04:00"))
        let est = try #require(Timestamp.parse("1998-10-25T01:30:00-05:00"))
        #expect(est.timeIntervalSince(edt) == 3600)
    }

    @Test func rejectsJunk() {
        #expect(Timestamp.parse("") == nil)
        #expect(Timestamp.parse("1998-11-11") == nil)
        #expect(Timestamp.parse("1998-11-11T19:33") == nil)
        #expect(Timestamp.parse("1998-13-11T19:33:15Z") == nil)
        #expect(Timestamp.parse("nonsense-and-more-stuff") == nil)
    }

    @Test func parsesCalendarDays() {
        #expect(Timestamp.day("1994-02-03") == CalendarDay(year: 1994, month: 2, day: 3))
        #expect(Timestamp.day("1994-2-3") == nil)
        #expect(Timestamp.day("") == nil)
    }
}
