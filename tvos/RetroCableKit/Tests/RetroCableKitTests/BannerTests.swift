import Foundation
import Testing
@testable import RetroCableKit

/// The info banner's text (TV-19, TV-20), the clock (TV-21) and the remote hints (D-12), from the
/// recorded /tune fixture.
@Suite struct BannerTests {
    let tune: Tune = {
        do { return try APIClient.decoder.decode(Tune.self, from: RepoFiles.fixture("tune.json")) } catch {
            fatalError("tune.json: \(error)")
        }
    }()

    var zone: String { "America/New_York" }

    func banner(_ slot: GuideSlot?, next: GuideSlot? = nil, year: Int = 1998, now: Date? = nil) -> BannerContent {
        BannerContent.make(channel: tune.channel, slot: slot, next: next, now: now ?? tune.now, year: year, zone: zone)
    }

    /// TV-19: number, call sign, title, episode line, description, progress, next, year.
    @Test func anEpisode() {
        let b = banner(tune.current.slot, next: tune.next[1].slot)
        #expect(b.number == "02")
        #expect(b.callSign == "KFIX FOX")
        #expect(b.title == "The Hendersons")
        #expect(b.episode == "Dad Gets Famous · S5 E12")
        #expect(b.badges.isEmpty)
        #expect(b.description == "A made-up family has a made-up week.")
        #expect(b.start == "7:33")
        #expect(b.end == "7:56")
        #expect(abs(b.progress - 679.058464 / 1376) < 0.0001)
        #expect(b.next == BannerContent.NextLine(time: "Next 7:56", title: "Fixture: The Movie"))
        #expect(b.year == "Year 1998")
    }

    /// TV-19: a movie's line is its year and running time; NEW and the holiday in upper case.
    @Test func aNewHolidayMovie() {
        let b = banner(tune.next[1].slot, now: tune.next[1].start.addingTimeInterval(60))
        #expect(b.title == "Fixture: The Movie")
        #expect(b.episode == "1998 · 1h 25m")
        #expect(b.badges == [.new, .holiday("HALLOWEEN")])
        #expect(b.badges.map(\.text) == ["NEW", "HALLOWEEN"])
        #expect(b.description == nil)
        #expect(b.next == nil)
    }

    @Test func aPretendPremiere() {
        var slot = tune.current.slot
        slot.transplant = true
        slot.isNew = true
        #expect(banner(slot).badges == [.new, .pretendPremiere])
        #expect(BannerContent.Badge.pretendPremiere.text == "PRETEND PREMIERE")
    }

    /// TV-20: a bumper shows the channel's name, nothing else.
    @Test func aBumperShowsTheChannelName() {
        var slot = tune.next[0].slot
        slot.description = "never shown"
        slot.isNew = true
        let b = banner(slot, next: tune.next[1].slot)
        #expect(b.title == "KFIX (Fixture)")
        #expect(b.episode == nil)
        #expect(b.badges.isEmpty)
        #expect(b.description == nil)
        #expect(b.next?.title == "Fixture: The Movie")
    }

    /// TV-20: filler shows only its title, even when it carries flags.
    @Test func fillerShowsOnlyItsTitle() {
        var slot = tune.next[2].slot
        slot.description = "never shown"
        slot.holidayName = "Halloween"
        let b = banner(slot)
        #expect(b.title == "Off the air")
        #expect(b.episode == nil)
        #expect(b.badges.isEmpty)
        #expect(b.description == nil)
    }

    /// Before the tune answers: "Tuning…", empty progress.
    @Test func beforeTheSlotIsKnown() {
        let b = banner(nil)
        #expect(b.title == "Tuning…")
        #expect(b.episode == nil)
        #expect(b.start == nil)
        #expect(b.end == nil)
        #expect(b.progress == 0)
        #expect(b.number == "02")
    }

    @Test func progressIsClamped() {
        let slot = tune.current.slot
        #expect(banner(slot, now: slot.start.addingTimeInterval(-50)).progress == 0)
        #expect(banner(slot, now: slot.end.addingTimeInterval(50)).progress == 1)
    }

    @Test func allYears() {
        #expect(banner(tune.current.slot, year: 0).year == "All Years")
        #expect(banner(tune.current.slot, year: 2005).year == "Year 2005")
    }

    @Test func anEmptyEpisodeLineIsNil() {
        var slot = tune.current.slot
        slot.episodeTitle = ""
        slot.season = nil
        slot.episode = nil
        #expect(banner(slot).episode == nil)
    }

    /// D-12: Siri Remote controls, never keyboard keys.
    @Test func remoteHintsNameSiriRemoteControls() {
        #expect(BannerContent.hints.map(\.key) == ["▲▼", "SELECT", "▶❚❚"])
        #expect(BannerContent.hints.map(\.action) == ["Channel", "Guide", "Last channel"])
        for hint in BannerContent.hints {
            for key in ["ENTER", "SPACE", "ESC", "Y"] { #expect(hint.key != key) }
        }
    }

    /// TV-21: the clock from simulated time in the server's zone.
    @Test func clockReadsSimulatedTimeInTheServersZone() {
        let clock = ClockText.make(tune.now, zone: zone)
        #expect(clock.time == "7:44 PM")
        #expect(clock.date == "Wednesday, Nov 11, 1998")
        #expect(ClockText.make(tune.now, zone: "America/Los_Angeles").time == "4:44 PM")
    }

    @Test func numberAndCallSign() {
        #expect(LiveText.number(2) == "02")
        #expect(LiveText.number(45) == "45")
        #expect(LiveText.number(120) == "120")
        #expect(LiveText.numberAndCallSign(tune.channel) == "02 KFIX")
    }

    /// TV-9's line, and nothing within 3 s.
    @Test func lengthMismatchLine() {
        let slot = tune.current.slot
        #expect(LiveText.lengthMismatch(slot, fileSeconds: 1381, tolerance: 3)
            == "The Hendersons S5E12: file runs 1381.0 s but its slot is 1376 s (+5.0 s)")
        #expect(LiveText.lengthMismatch(slot, fileSeconds: 1379, tolerance: 3) == nil)
        #expect(LiveText.lengthMismatch(slot, fileSeconds: 1372.96, tolerance: 3)
            == "The Hendersons S5E12: file runs 1373.0 s but its slot is 1376 s (-3.0 s)")
    }

    /// TV-10: the bumper slate and the demo slate.
    @Test func slates() {
        let bumper = BumperSlate.make(channel: tune.channel, next: tune.next[1].slot, zone: zone)
        #expect(bumper == BumperSlate(callSign: "02 KFIX", name: "KFIX (Fixture)", upNext: "Up next 7:56 Fixture: The Movie"))
        #expect(BumperSlate.make(channel: tune.channel, next: nil, zone: zone).upNext == nil)
        #expect(DemoSlate.make(tune.current, channel: tune.channel, next: nil) == nil)
        #expect(DemoSlate.make(tune.next[1], channel: tune.channel, next: nil)
            == DemoSlate(title: "Fixture: The Movie", detail: "Feature presentation"))
        var demoBumper = tune.next[0]
        demoBumper.mediaPath = SignedPath("/media/_demo/bumper.mp4")
        #expect(DemoSlate.make(demoBumper, channel: tune.channel, next: tune.next[1].slot)
            == DemoSlate(title: "02 KFIX", detail: "Up next: Fixture: The Movie"))
        #expect(DemoSlate.make(demoBumper, channel: tune.channel, next: nil)?.detail == "Stay tuned")
    }
}
