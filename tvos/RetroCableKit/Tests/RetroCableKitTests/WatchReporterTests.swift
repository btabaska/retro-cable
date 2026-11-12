import Foundation
import Testing
@testable import RetroCableKit

/// web/src/lib/watch.test.ts, plus the tvOS differences: `device: "appletv"` and power-off on
/// background instead of a beacon (TV-34, TV-35, TV-36).
@MainActor
@Suite struct WatchReporterTests {
    static let start = Timestamp.parse("1995-10-31T20:00:00-05:00")!

    @MainActor
    final class Harness {
        var now: TimeInterval = 1_000
        var sent: [WatchReport] = []
        var nextID = 0
        lazy var reporter = WatchReporter(
            send: { [unowned self] in self.sent.append($0) },
            now: { [unowned self] in self.now },
            makeID: { [unowned self] in
                self.nextID += 1
                return "session-\(self.nextID)"
            }
        )

        func tick(_ seconds: TimeInterval) { now += seconds }
    }

    func watching(
        channel: String = "ktvu", year: Int = 1995, start: Date = start, kind: SlotKind = .episode,
        show: String? = "the-hendersons", season: Int? = 4, episode: Int? = 12, seconds: Int = 1320
    ) -> Watching {
        Watching(
            channel: channel, year: year, slotStart: start, seconds: seconds, kind: kind, show: show,
            season: season, episode: episode, simNow: Self.start.addingTimeInterval(90)
        )
    }

    @Test func reportsAProgramWithHeartbeatsThenOnceMoreWhenTheNextOneStarts() {
        let h = Harness()
        h.reporter.watch(watching())
        h.tick(60)
        h.reporter.heartbeat()
        #expect(h.sent.count == 1)
        #expect(
            h.sent.first == WatchReport(
                session: "session-1", channel: "ktvu", item: "the-hendersons", kind: .episode, season: 4, episode: 12,
                year: 1995, offset: 90, seconds: 60, runtime: 1320, ended: nil, device: "appletv"
            )
        )
        h.tick(600)
        h.reporter.watch(watching())
        #expect(h.sent.count == 1)
        h.reporter.watch(watching(start: Self.start.addingTimeInterval(1320), show: "x-fixtures"))
        #expect(h.sent.count == 2)
        #expect(h.sent.last?.session == "session-1")
        #expect(h.sent.last?.seconds == 660)
        #expect(h.sent.last?.ended == .programEnd)
    }

    @Test func callsChangingChannelsOrYearsATuneAwayAndNeverReportsSurfing() {
        let h = Harness()
        h.reporter.watch(watching())
        h.tick(TimeInterval(WatchReporter.minSeconds - 1))
        h.reporter.watch(watching(channel: "kpix"))
        #expect(h.sent.isEmpty)
        h.tick(200)
        h.reporter.watch(watching(channel: "kpix", year: 2001))
        #expect(h.sent.first?.channel == "kpix")
        #expect(h.sent.first?.ended == .tuneAway)
        #expect(h.sent.first?.seconds == 200)
        h.tick(30)
        h.reporter.watch(nil)
        #expect(h.sent.last?.year == 2001)
        #expect(h.sent.last?.ended == .tuneAway)
    }

    @Test func endsWithProgramEndOnABumperOfTheSameChannelAndIgnoresFiller() {
        let h = Harness()
        h.reporter.watch(watching())
        h.tick(1230)
        h.reporter.watch(watching(start: Self.start.addingTimeInterval(1320), kind: .bumper, show: nil))
        #expect(h.sent.first?.ended == .programEnd)
        h.tick(600)
        h.reporter.watch(watching(start: Self.start.addingTimeInterval(1380), kind: .filler, show: nil))
        h.reporter.heartbeat()
        #expect(h.sent.count == 1)
    }

    @Test func capsSecondsAtTheRuntimeAndEndsOnlyOnce() {
        let h = Harness()
        h.reporter.watch(watching())
        h.tick(5000)
        h.reporter.heartbeat()
        #expect(h.sent.first?.seconds == 1320)
        #expect(h.sent.first?.ended == nil)
        h.reporter.end(.powerOff)
        h.reporter.end(.powerOff)
        h.reporter.heartbeat()
        #expect(h.sent.count == 2)
        #expect(h.sent.last?.ended == .powerOff)
    }

    @Test func capsTheOffsetAtTheRuntime() {
        let h = Harness()
        var late = watching(seconds: 60)
        late.simNow = Self.start.addingTimeInterval(500)
        h.reporter.watch(late)
        h.tick(30)
        h.reporter.heartbeat()
        #expect(h.sent.first?.offset == 60)
        #expect(h.sent.first?.runtime == 60)
    }

    @Test func movieKindAndNoShowIsNeverAProgram() {
        let h = Harness()
        h.reporter.watch(watching(kind: .movie, season: nil, episode: nil))
        h.tick(20)
        h.reporter.heartbeat()
        #expect(h.sent.first?.kind == .movie)
        let h2 = Harness()
        h2.reporter.watch(watching(show: nil))
        h2.tick(100)
        h2.reporter.heartbeat()
        #expect(h2.sent.isEmpty)
    }

    @Test func sessionIDsAre32Hex() {
        let id = WatchReporter.randomSessionID()
        #expect(id.count == 32)
        #expect(id.allSatisfy { "0123456789abcdef".contains($0) })
        #expect(WatchReporter.randomSessionID() != id)
    }

    /// TV-36: a failed post is dropped silently and never blocks the caller.
    @Test func postingNeverThrowsOrWaits() async {
        let api = APIClient(baseURL: URL(string: "https://tv.example.test")!, transport: fixtureTransport(failing: true))
        let send = WatchReporter.poster(api)
        send(WatchReport(
            session: "0123456789abcdef0123456789abcdef", channel: "k", item: "x", kind: .episode, season: nil,
            episode: nil, year: 1998, offset: 0, seconds: 10, runtime: 100, ended: nil, device: "appletv"
        ))
    }
}
