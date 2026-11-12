import Foundation
import Testing
@testable import RetroCableKit

/// The tune queue and its player (TV-7 to TV-12, TV-14, TV-40, TV-41) on a fake clock, a fake
/// player and a /tune server whose answers the test hands out.
@MainActor
final class TunerRig {
    let clock = ManualClock()
    let engine = FakeEngine()
    let server = TuneServer()
    var logs: [String] = []
    lazy var tuner = Tuner(
        fetch: server.fetch,
        engine: engine,
        clock: clock,
        timers: clock,
        mediaURL: { URL(string: $0.raw, relativeTo: URL(string: "https://tv.example.test")!)?.absoluteURL },
        log: { [unowned self] in self.logs.append($0) }
    )

    let ktvu = Make.channel("ktvu", number: 2, callSign: "KTVU", network: "FOX")
    let kpix = Make.channel("kpix", number: 5, callSign: "KPIX", network: "CBS")

    /// Tunes `channel` and answers with `slots` at `now` minutes past 7:30.
    func tuned(_ channel: ChannelInfo? = nil, now: Double = 5, _ slots: [TunedSlot] = Make.evening()) async {
        let channel = channel ?? ktvu
        let before = server.requests.count
        tuner.tune(channel, year: 1998)
        await server.waitFor(before + 1)
        server.answerLast(Make.tune(channel, now: now, slots))
        await tuner.settled()
    }
}

@MainActor
@Suite struct TunerTests {
    /// TV-7: the position is worked out when the file is ready, not when /tune answered.
    @Test func startsAtTheLivePositionWhenTheFileIsReady() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        #expect(rig.server.requests == [.init(channel: "ktvu", year: 1998)])
        #expect(rig.engine.loads == ["/media/tv/show-1.mp4"])
        #expect(rig.tuner.picture == .black)
        #expect(rig.tuner.slot?.title == "Show 1")
        rig.clock.advance(2)
        rig.engine.play(duration: 23 * 60)
        #expect(rig.engine.starts == [302])
        #expect(rig.tuner.picture == .video)
    }

    /// TV-7: never past 0.25 s before the end of the file.
    @Test func clampsTheStartBeforeTheEndOfTheFile() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 100)
        #expect(rig.engine.starts == [99.75])
    }

    /// TV-8: at slot end the next slot plays with no input.
    @Test func advancesAtSlotEnd() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 23 * 60)
        rig.clock.advance(18 * 60 - 1)
        #expect(rig.engine.loads.count == 1)
        rig.clock.advance(1.1)
        #expect(rig.engine.loads == ["/media/tv/show-1.mp4", "/media/tv/show-2.mp4"])
        #expect(rig.tuner.slot?.title == "Show 2")
        #expect(rig.tuner.picture == .black)
        rig.clock.advance(1)
        rig.engine.play(duration: 23 * 60)
        // It starts at its own beginning plus the time it took to load.
        #expect(abs(rig.engine.starts.last! - 1.1) < 0.001)
    }

    /// TV-8: a file with at most 5 s left finishes before the next slot.
    @Test func letsAFileWithAFewSecondsLeftFinish() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 23 * 60)
        rig.engine.left = 3
        rig.clock.advance(18 * 60 + 0.1)
        #expect(rig.engine.loads.count == 1)
        rig.engine.send(.ended(id: rig.engine.lastLoadID!))
        #expect(rig.engine.loads.count == 2)
        #expect(rig.tuner.slot?.title == "Show 2")
    }

    @Test func aFileThatNeverReportsItsEndStillAdvances() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 23 * 60)
        rig.engine.left = 3
        rig.clock.advance(18 * 60 + 0.1)
        #expect(rig.engine.loads.count == 1)
        rig.clock.advance(3.3)
        #expect(rig.engine.loads.count == 2)
    }

    @Test func aFileFarBehindTheScheduleDoesNotHoldTheChannel() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 23 * 60)
        rig.engine.left = 30
        rig.clock.advance(18 * 60 + 0.1)
        #expect(rig.engine.loads.count == 2)
    }

    /// TV-8: the queue is refetched when the slot playing is the second to last one fetched, and
    /// the answer, which restarts at the slot playing now, doesn't restart it.
    @Test func refetchesBeforeTheQueueRunsOutWithoutRestartingTheSlot() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 23 * 60)
        rig.clock.advance(18 * 60 + 0.1)
        rig.engine.play(duration: 23 * 60)
        #expect(rig.server.requests.count == 1)
        rig.clock.advance(23 * 60)
        await rig.server.waitFor(2)
        #expect(rig.tuner.slot?.title == "Show 3")
        #expect(rig.server.requests.count == 2)
        rig.engine.play(duration: 23 * 60)
        let loads = rig.engine.loads.count
        let evening = Make.evening()
        rig.server.answerLast(Make.tune(rig.ktvu, now: 47, Array(evening[2...]) + Make.evening("Late", from: 92, count: 3)))
        await rig.tuner.settled()
        #expect(rig.engine.loads.count == loads)
        #expect(rig.tuner.slot?.title == "Show 3")
        #expect(rig.tuner.upcoming.map(\.title) == ["Show 4", "Late 1", "Late 2", "Late 3"])
        #expect(rig.tuner.picture == .video)
    }

    /// TV-8: an evening plays back to back: every slot loads in order with no gap.
    @Test func anEveningPlaysBackToBack() async {
        let rig = TunerRig()
        let slots = Make.evening(count: 12)
        await rig.tuned(now: 0, Array(slots[0..<4]))
        var served = 4
        for _ in 0..<11 {
            rig.engine.play(duration: 23 * 60)
            rig.clock.advance(23 * 60 + 0.1)
            await rig.server.waitFor(rig.server.requests.count + 1)
            if rig.server.open.count == 1, let playing = slots.firstIndex(where: { $0.start == rig.tuner.slot?.start }) {
                let end = min(slots.count, playing + 4)
                served = end
                rig.server.answerLast(Make.tune(rig.ktvu, now: Double(playing * 23) + 0.2, Array(slots[playing..<end])))
                await rig.tuner.settled()
            }
        }
        #expect(served == 12)
        #expect(rig.engine.loads == (1...12).map { "/media/tv/show-\($0).mp4" })
    }

    /// TV-9: a length mismatch over 3 s is logged with title, SxEy and both lengths, never the URL.
    @Test func logsALengthMismatchWithoutTheURL() async {
        let rig = TunerRig()
        let slot = Make.slot("The Hendersons", at: 0, seconds: 1376, season: 5, episode: 12)
        await rig.tuned(now: 5, [slot])
        rig.engine.play(duration: 1381)
        #expect(rig.logs.filter { $0.contains("file runs") } == [
            "The Hendersons S5E12: file runs 1381.0 s but its slot is 1376 s (+5.0 s)",
        ])
        #expect(rig.engine.starts == [300])
    }

    @Test func aSmallMismatchIsNotLogged() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 23 * 60 - 2.5)
        #expect(rig.logs.allSatisfy { !$0.contains("file runs") })
    }

    @Test func aShortFileLogsANegativeMismatchAndAMovieHasNoEpisode() async {
        let rig = TunerRig()
        let movie = Make.slot("Fixture Movie", at: 0, seconds: 5100, kind: .movie)
        await rig.tuned(now: 5, [movie])
        rig.engine.play(duration: 5090.5)
        #expect(rig.logs.first == "Fixture Movie: file runs 5090.5 s but its slot is 5100 s (-9.5 s)")
    }

    /// TV-10: filler shows color bars and its title.
    @Test func fillerShowsItsTitle() async {
        let rig = TunerRig()
        await rig.tuned(now: 1, [Make.slot("Off the air", at: 0, seconds: 300, kind: .filler, media: nil)])
        #expect(rig.tuner.picture == .filler(title: "Off the air"))
        #expect(rig.engine.loads.isEmpty)
        #expect(rig.engine.calls.last == .stop)
    }

    /// TV-10: a bumper without media shows the station ID slate; the next program skips bumpers.
    @Test func aBumperWithoutMediaShowsTheStationSlate() async {
        let rig = TunerRig()
        await rig.tuned(now: 0.1, [
            Make.slot("KTVU", at: 0, seconds: 10, kind: .bumper, media: nil),
            Make.slot("KTVU", at: 10.0 / 60, seconds: 10, kind: .bumper, media: nil),
            Make.slot("Seinfeld Like", at: 20.0 / 60, seconds: 1320),
        ])
        #expect(rig.tuner.picture == .bumperSlate(BumperSlate(
            callSign: "02 KTVU", name: "KTVU Channel", upNext: "Up next 7:30 Seinfeld Like"
        )))
    }

    @Test func aBumperWithMediaPlays() async {
        let rig = TunerRig()
        await rig.tuned(now: 0.05, [
            Make.slot("KTVU", at: 0, seconds: 10, kind: .bumper, media: "/media/bumpers/ktvu.mp4"),
            Make.slot("Next Show", at: 10.0 / 60, seconds: 1320),
        ])
        #expect(rig.engine.loads == ["/media/bumpers/ktvu.mp4"])
        rig.engine.play(duration: 10)
        #expect(rig.tuner.picture == .video)
        #expect(rig.tuner.demoSlate == nil)
    }

    /// TV-10: demo feeds carry the browser's slate; real media never does.
    @Test func demoFeedsGetTheSlate() async {
        let rig = TunerRig()
        await rig.tuned(now: 1, [
            Make.slot("The Hendersons", at: 0, seconds: 1320, media: "/media/_demo/x.mp4", season: 5, episode: 12,
                      episodeTitle: "Dad Gets Famous"),
        ])
        #expect(rig.tuner.demoSlate == DemoSlate(title: "The Hendersons", detail: "S5 · E12 — Dad Gets Famous"))
        let real = TunerRig()
        await real.tuned(now: 1, [Make.slot("The Hendersons", at: 0, seconds: 1320, season: 5, episode: 12)])
        #expect(real.tuner.demoSlate == nil)
    }

    /// TV-11: one fresh retune at the live position, then the filler card until the slot ends.
    @Test func aFailedFileRetunesOnceThenShowsTheFillerCard() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.send(.failed(id: rig.engine.lastLoadID!))
        await rig.server.waitFor(2)
        #expect(rig.server.requests.count == 2)
        #expect(rig.tuner.picture == .black)
        rig.clock.advance(1)
        rig.server.answerLast(Make.tune(rig.ktvu, now: 6, Make.evening()))
        await rig.tuner.settled()
        #expect(rig.engine.loads.count == 2)
        rig.engine.send(.ready(id: rig.engine.lastLoadID!, duration: 23 * 60))
        #expect(rig.engine.starts == [360])
        rig.engine.send(.failed(id: rig.engine.lastLoadID!))
        #expect(rig.server.requests.count == 2)
        #expect(rig.tuner.picture == .filler(title: "Show 1"))
        rig.clock.advance(17 * 60 + 0.1)
        #expect(rig.tuner.slot?.title == "Show 2")
        #expect(rig.engine.loads.count == 3)
        #expect(rig.tuner.picture == .black)
    }

    @Test func eventsFromAnOldFileAreIgnored() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        let old = rig.engine.lastLoadID!
        rig.engine.play(duration: 23 * 60)
        rig.clock.advance(18 * 60 + 0.1)
        rig.engine.send(.failed(id: old))
        rig.engine.send(.ready(id: old, duration: 10))
        #expect(rig.server.requests.count == 1)
        #expect(rig.engine.starts.count == 1)
    }

    /// TV-12: /tune failing keeps the channel, leaves the slot unknown ("Tuning…") and retries every 5 s.
    @Test func aFailedTuneRetriesEveryFiveSeconds() async {
        let rig = TunerRig()
        rig.tuner.tune(rig.ktvu, year: 1998)
        await rig.server.waitFor(1)
        rig.server.failLast()
        await rig.tuner.settled()
        #expect(rig.tuner.channel?.id == "ktvu")
        #expect(rig.tuner.slot == nil)
        #expect(rig.clock.waiting == [5])
        rig.clock.advance(4.9)
        #expect(rig.server.requests.count == 1)
        rig.clock.advance(0.1)
        await rig.server.waitFor(2)
        #expect(rig.server.requests.count == 2)
        rig.server.failLast()
        await rig.tuner.settled()
        rig.clock.advance(5)
        await rig.server.waitFor(3)
        #expect(rig.server.requests.count == 3)
        rig.server.answerLast(Make.tune(rig.ktvu, now: 5, Make.evening()))
        await rig.tuner.settled()
        #expect(rig.tuner.slot?.title == "Show 1")
    }

    /// TV-12 / TV-5: a /tune that takes 3 s to fail is retried 2 s later: tries start 5 s apart.
    @Test func aSlowTuneFailureRetriesFiveSecondsAfterItStarted() async {
        let rig = TunerRig()
        rig.tuner.tune(rig.ktvu, year: 1998)
        await rig.server.waitFor(1)
        rig.clock.advance(3)
        rig.server.failLast()
        await rig.tuner.settled()
        #expect(rig.clock.waiting.contains(2))
        rig.clock.advance(2)
        await rig.server.waitFor(2)
        #expect(rig.server.requests.count == 2)
        // A try that hangs past 5 s goes again at once.
        rig.clock.advance(8)
        rig.server.failLast()
        await rig.tuner.settled()
        rig.clock.advance(0)
        await rig.server.waitFor(3)
        #expect(rig.server.requests.count == 3)
    }

    /// TV-14: five quick presses land on the fifth channel; the old sound stops on the first
    /// press, and answers for channels flipped past are dropped.
    @Test func rapidFlippingPlaysOnlyTheLastChannel() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 23 * 60)
        rig.engine.calls = []
        let channels = (1...5).map { Make.channel("c\($0)", number: 10 + $0) }
        for (i, channel) in channels.enumerated() {
            rig.tuner.tune(channel, year: 1998)
            await rig.server.waitFor(i + 2)
        }
        #expect(rig.engine.calls.first == .stop)
        #expect(rig.server.requests.map(\.channel) == ["ktvu", "c1", "c2", "c3", "c4", "c5"])
        rig.server.answer(5, Make.tune(channels[4], now: 5, Make.evening("Five")))
        await settle()
        #expect(rig.tuner.slot?.title == "Five 1")
        for (index, channel) in channels.prefix(4).enumerated() {
            rig.server.answer(index + 1, Make.tune(channel, now: 5, Make.evening("C\(index)")))
        }
        await rig.tuner.settled()
        #expect(rig.tuner.channel?.id == "c5")
        #expect(rig.engine.loads == ["/media/tv/five-1.mp4"])
        #expect(rig.tuner.slot?.title == "Five 1")
    }

    @Test func tuningStopsTheOldPictureAtOnce() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 23 * 60)
        rig.tuner.tune(rig.kpix, year: 1998)
        #expect(rig.engine.calls.last == .stop)
        #expect(rig.tuner.picture == .black)
        #expect(rig.tuner.slot == nil)
        #expect(rig.tuner.channel?.id == "kpix")
    }

    /// TV-40: each tune's press-to-first-frame time is logged with the channel id only.
    @Test func logsPressToFirstFrame() async {
        let rig = TunerRig()
        rig.tuner.tune(rig.kpix, year: 1998)
        await rig.server.waitFor(1)
        rig.clock.advance(0.4)
        rig.server.answerLast(Make.tune(rig.kpix, now: 5, Make.evening()))
        await rig.tuner.settled()
        rig.clock.advance(0.45)
        rig.engine.play(duration: 23 * 60)
        #expect(rig.logs == ["tune kpix 850 ms"])
        #expect(rig.tuner.latency.entries == [TuneLatency(channel: "kpix", milliseconds: 850)])
        // The next slot is not a tune.
        rig.clock.advance(18 * 60 + 0.1)
        rig.engine.play(duration: 23 * 60)
        #expect(rig.logs.count == 1)
    }

    @Test func aSlateCountsAsTheFirstFrame() async {
        let rig = TunerRig()
        rig.tuner.tune(rig.kpix, year: 1998)
        await rig.server.waitFor(1)
        rig.clock.advance(0.2)
        rig.server.answerLast(Make.tune(rig.kpix, now: 1, [Make.slot("Off", at: 0, seconds: 300, kind: .filler, media: nil)]))
        await rig.tuner.settled()
        #expect(rig.logs == ["tune kpix 200 ms"])
    }

    @Test func theLatencyLogKeepsTheLastFifty() {
        var log = TuneLatencyLog()
        for i in 0..<60 { log.record(TuneLatency(channel: "c\(i)", milliseconds: i)) }
        #expect(log.entries.count == 50)
        #expect(log.entries.first?.channel == "c10")
        #expect(log.p95 == 57)
    }

    /// TV-41: nothing the tuner logs carries a media path, exp or sig.
    @Test func logsNeverCarryAMediaPath() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 2000)
        rig.engine.send(.failed(id: rig.engine.lastLoadID!))
        for line in rig.logs {
            for secret in ["/media", "sig", "exp=", "https://"] {
                #expect(!line.contains(secret), "\(line)")
            }
        }
        #expect(!rig.logs.isEmpty)
    }

    /// Background and the year picker: everything stops, nothing is left scheduled.
    @Test func stopSilencesAndCancelsEverything() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.engine.play(duration: 23 * 60)
        rig.tuner.stop()
        #expect(rig.engine.calls.last == .stop)
        #expect(rig.clock.waiting.isEmpty)
        #expect(rig.tuner.slot == nil)
        #expect(rig.tuner.picture == .black)
        rig.clock.advance(3600)
        #expect(rig.engine.loads.count == 1)
    }

    @Test func simulatedNowRunsOnFromTheAnswer() async {
        let rig = TunerRig()
        await rig.tuned(now: 5)
        rig.clock.advance(30)
        #expect(rig.tuner.simNow() == Make.base.addingTimeInterval(5 * 60 + 30))
    }
}
