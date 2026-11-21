import Foundation
import Testing
@testable import RetroCableKit

@Suite struct APIClientTests {
    let base = URL(string: "https://tv.example.test")!

    func query(_ request: URLRequest?) -> [String: String] {
        let items = URLComponents(url: request!.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        return Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
    }

    @Test func fetchesTheLineupForAYear() async throws {
        let transport = fixtureTransport()
        let api = APIClient(baseURL: base, transport: transport)
        let lineup = try await api.channels(year: 0)
        #expect(lineup.channels.count == 4)
        let request = try #require(transport.requests.first)
        #expect(request.httpMethod == "GET")
        #expect(request.url?.path == "/api/channels")
        #expect(query(request) == ["year": "0"])
        #expect(request.cachePolicy == .reloadIgnoringLocalCacheData)
        _ = try await api.channels(year: 1998, date: "09-02")
        #expect(query(transport.requests.last) == ["year": "1998", "date": "09-02"])
    }

    @Test func asksTheGuideForThirteenAndAHalfHours() async throws {
        let transport = fixtureTransport()
        let api = APIClient(baseURL: base, transport: transport)
        _ = try await api.guide(year: 1998, date: "11-11", start: "19:30", hours: 13.5)
        #expect(transport.requests.last?.url?.path == "/api/guide")
        #expect(query(transport.requests.last) == ["year": "1998", "date": "11-11", "start": "19:30", "hours": "13.5"])
        _ = try await api.guide(year: 1998, date: "11-11", start: "19:30", hours: 24)
        #expect(query(transport.requests.last)["hours"] == "24")
    }

    @Test func tunesAChannel() async throws {
        let transport = fixtureTransport()
        let api = APIClient(baseURL: base, transport: transport)
        let tune = try await api.tune(channel: "kfix", year: 1998)
        #expect(tune.current.title == "The Hendersons")
        #expect(transport.requests.last?.url?.path == "/api/tune/kfix")
        #expect(query(transport.requests.last) == ["year": "1998", "count": "3"])
    }

    /// TV-5: a host that doesn't answer at all (powered off) gives up on the quick calls after
    /// 4 s, so a 5 s retry cadence holds; the bigger /guide answer gets 8 s.
    @Test func quickCallsTimeOutAfterFourSeconds() async throws {
        let transport = fixtureTransport()
        let api = APIClient(baseURL: base, transport: transport)
        _ = try await api.version()
        _ = try await api.channels(year: 1998)
        _ = try await api.tune(channel: "kfix", year: 1998)
        _ = try await api.guide(year: 1998, date: "11-11", start: "19:30", hours: 13.5)
        #expect(transport.requests.map(\.timeoutInterval) == [4, 4, 4, 8])
        #expect(APIClient.quickTimeout < AppModel.retrySeconds)
    }

    @Test func readsTheServerVersion() async throws {
        let api = APIClient(baseURL: base, transport: fixtureTransport())
        let version = try await api.version()
        #expect(version.label == "Server v0.0.0 · c8e0e3d")
    }

    @Test func postsAWatchReportAndTakes204() async throws {
        let transport = StubTransport(status: 204)
        let api = APIClient(baseURL: base, transport: transport)
        let report = WatchReport(
            session: "0123456789abcdef0123456789abcdef", channel: "kfix", item: "the-hendersons", kind: .episode,
            season: 5, episode: 12, year: 1998, offset: 0, seconds: 60, runtime: 1376, ended: nil, device: "appletv"
        )
        try await api.postWatch(report)
        let request = try #require(transport.requests.first)
        #expect(request.httpMethod == "POST")
        #expect(request.url?.path == "/api/watch")
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
        let body = try #require(request.httpBody)
        let object = try #require(try JSONSerialization.jsonObject(with: body) as? [String: Any])
        #expect(object["session"] as? String == "0123456789abcdef0123456789abcdef")
    }

    @Test func mapsFailuresToErrorsWithoutTheURL() async throws {
        let api = APIClient(baseURL: base, transport: StubTransport(status: 503))
        await #expect(throws: APIError.status(503)) { try await api.version() }
        let offline = APIClient(baseURL: base, transport: fixtureTransport(failing: true))
        await #expect(throws: APIError.unreachable) { try await offline.channels(year: 1998) }
        let junk = APIClient(baseURL: base, transport: StubTransport(status: 200, body: Data("{}".utf8)))
        await #expect(throws: APIError.badResponse) { try await junk.tune(channel: "kfix", year: 1998) }
        for error in [APIError.status(503), .unreachable, .badResponse] {
            #expect(!String(describing: error).contains("http"))
        }
    }

    @Test func escapesChannelIdsInThePath() async throws {
        let transport = fixtureTransport()
        let api = APIClient(baseURL: base, transport: transport)
        _ = try? await api.tune(channel: "a b/c", year: 1998)
        #expect(transport.requests.last?.url?.absoluteString.contains("/api/tune/a%20b%2Fc") == true)
    }

    @Test func resolvesSignedMediaPathsAgainstTheServer() throws {
        let tune = try APIClient.decoder.decode(Tune.self, from: RepoFiles.fixture("tune.json"))
        let api = APIClient(baseURL: base, transport: StubTransport())
        let path = try #require(tune.current.mediaPath)
        let url = try #require(api.mediaURL(path))
        #expect(url.host == "tv.example.test")
        #expect(url.scheme == "https")
        #expect(url.path.hasPrefix("/media/"))
    }
}

@Suite struct ServerConfigTests {
    @Test func defaultsToTheHomelab() {
        #expect(ServerConfig.resolve(info: nil, arguments: [], environment: [:]).absoluteString == "https://tv.home.tabaska.us")
        #expect(ServerConfig.defaultURL.absoluteString == "https://tv.home.tabaska.us")
    }

    @Test func readsInfoPlistThenLaunchArgumentThenEnvironment() {
        let info = "https://tv.other.test"
        #expect(ServerConfig.resolve(info: info, arguments: [], environment: [:]).absoluteString == info)
        #expect(
            ServerConfig.resolve(info: info, arguments: ["app", "-RCServerURL", "http://127.0.0.1:8080"], environment: [:])
                .absoluteString == "http://127.0.0.1:8080"
        )
        #expect(
            ServerConfig.resolve(info: info, arguments: [], environment: ["RC_SERVER_URL": "http://127.0.0.1:8080/"])
                .absoluteString == "http://127.0.0.1:8080"
        )
    }

    @Test func ignoresJunk() {
        #expect(ServerConfig.resolve(info: "not a url", arguments: ["-RCServerURL"], environment: [:]) == ServerConfig.defaultURL)
        #expect(ServerConfig.resolve(info: "ftp://x", arguments: [], environment: [:]) == ServerConfig.defaultURL)
    }
}
