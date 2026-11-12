import Foundation
import Testing
@testable import RetroCableKit

@Suite struct ModelTests {
    func decode<T: Decodable>(_ type: T.Type, _ fixture: String) throws -> T {
        try APIClient.decoder.decode(T.self, from: RepoFiles.fixture(fixture))
    }

    @Test func decodesTheLineup() throws {
        let lineup = try decode(Lineup.self, "channels.json")
        #expect(lineup.year == 1998)
        #expect(lineup.date == "1998-11-11")
        #expect(lineup.weekday == "Wednesday")
        #expect(lineup.timezone == "America/New_York")
        #expect(abs(lineup.now.timeIntervalSince1970 - 910_831_469.090069) < 0.000_01)
        #expect(lineup.channels.map(\.id) == ["kfix", "retro-classics", "late-toons", "fixture-movies"])
        let kfix = try #require(lineup.channels.first)
        #expect(kfix.callSign == "KFIX")
        #expect(kfix.network == "FOX")
        #expect(kfix.type == .network)
        #expect(kfix.onAir)
        #expect(lineup.channels[2].launch == "2001-09-02")
        #expect(!lineup.channels[2].onAir)
        #expect(lineup.channels[1].library)
        #expect(lineup.channels[3].type == .movie)
    }

    @Test func decodesATune() throws {
        let tune = try decode(Tune.self, "tune.json")
        #expect(tune.channel.id == "kfix")
        #expect(tune.offsetSeconds == 679.058464)
        #expect(tune.current.kind == .episode)
        #expect(tune.current.title == "The Hendersons")
        #expect(tune.current.episodeTitle == "Dad Gets Famous")
        #expect(tune.current.season == 5)
        #expect(tune.current.episode == 12)
        #expect(tune.current.airDate == "1994-02-03")
        #expect(tune.current.seconds == 1376)
        #expect(tune.current.end.timeIntervalSince(tune.current.start) == 1376)
        #expect(tune.current.mediaPath != nil)
        #expect(tune.current.mediaPath?.isDemoFeed == false)
        #expect(tune.next.map(\.kind) == [.bumper, .movie, .filler])
        // A slot without `description` (the bumper) reads as empty, the contract's default.
        #expect(tune.next[0].description == "")
        #expect(tune.next[0].mediaPath == nil)
        #expect(tune.next[1].isNew)
        #expect(tune.next[1].holidayName == "Halloween")
        #expect(tune.next[1].mediaPath?.isDemoFeed == true)
        #expect(tune.next[2].transplant)
    }

    @Test func decodesTheGuide() throws {
        let guide = try decode(Guide.self, "guide.json")
        #expect(guide.timezone == "America/New_York")
        #expect(guide.end.timeIntervalSince(guide.start) == 5400)
        #expect(guide.channels.first?.slots.count == 2)
        #expect(guide.channels.first?.slots.first?.description == "")
        #expect(guide.channels.first?.slots.last?.transplant == true)
    }

    @Test func decodesTheVersion() throws {
        let version = try decode(ServerVersion.self, "version.json")
        #expect(version == ServerVersion(version: "0.0.0", commit: "c8e0e3d"))
    }

    /// TV-41: a signed media path never reaches a description, a debug print or a dump.
    @Test func signedPathsNeverPrint() throws {
        let tune = try decode(Tune.self, "tune.json")
        var dumped = ""
        dump(tune, to: &dumped)
        for text in [String(describing: tune), String(reflecting: tune), dumped, "\(tune.current)"] {
            #expect(!text.contains("/media/"))
            #expect(!text.contains(".mp4"))
        }
    }

    /// TV-41: fixtures carry no signature, expiry or token.
    @Test func fixturesCarryNoSecrets() throws {
        let folder = RepoFiles.root.appendingPathComponent("tvos/RetroCableKit/Tests/RetroCableKitTests/Fixtures")
        for name in try FileManager.default.contentsOfDirectory(atPath: folder.path) {
            let text = try String(contentsOf: folder.appendingPathComponent(name), encoding: .utf8)
            for secret in ["sig=", "exp=", "token", "api_key"] {
                #expect(!text.lowercased().contains(secret), "\(name) contains \(secret)")
            }
        }
    }

    @Test func watchReportEncodesTheContractKeys() throws {
        let report = WatchReport(
            session: "0123456789abcdef0123456789abcdef", channel: "kfix", item: "the-hendersons",
            kind: .episode, season: 5, episode: 12, year: 0, offset: 90, seconds: 60, runtime: 1376,
            ended: .programEnd, device: "appletv"
        )
        let object = try #require(try JSONSerialization.jsonObject(with: JSONEncoder().encode(report)) as? [String: Any])
        #expect(object["ended"] as? String == "program_end")
        #expect(object["device"] as? String == "appletv")
        #expect(object["kind"] as? String == "episode")
        #expect(object["year"] as? Int == 0)
        // An open session sends `ended: null` explicitly, as the browser does.
        let open = WatchReport(
            session: "0123456789abcdef0123456789abcdef", channel: "kfix", item: "x", kind: .movie,
            season: nil, episode: nil, year: 1998, offset: 0, seconds: 10, runtime: 100, ended: nil, device: "appletv"
        )
        let openObject = try #require(try JSONSerialization.jsonObject(with: JSONEncoder().encode(open)) as? [String: Any])
        #expect(openObject["ended"] is NSNull)
    }
}
