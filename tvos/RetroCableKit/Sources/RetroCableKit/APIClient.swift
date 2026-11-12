import Foundation

/// Sends one HTTP request. URLSession in the app; a stub in tests.
public protocol HTTPTransport: Sendable {
    func data(for request: URLRequest) async throws -> (Data, URLResponse)
}

public struct URLSessionTransport: HTTPTransport {
    let session: URLSession

    public init(session: URLSession = URLSessionTransport.makeSession()) {
        self.session = session
    }

    /// No cookies, no disk cache, short timeouts: every answer is about simulated now.
    public static func makeSession() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 8
        config.timeoutIntervalForResource = 20
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }

    public func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        try await session.data(for: request)
    }
}

/// What can go wrong talking to the server. It never carries the URL or the underlying error,
/// whose text can include a signed path, so an `APIError` is always safe to log.
public enum APIError: Error, Equatable, Sendable, CustomStringConvertible {
    /// No answer: offline, refused, timed out, TLS failure.
    case unreachable
    /// An HTTP status other than the one expected.
    case status(Int)
    /// An answer that isn't the contract's JSON.
    case badResponse

    public var description: String {
        switch self {
        case .unreachable: "server unreachable"
        case .status(let code): "server answered \(code)"
        case .badResponse: "server answer did not decode"
        }
    }
}

/// The TV API under `/api` (docs/specs/api.md). Read calls are GETs; `postWatch` is fire and forget
/// for its callers (WatchReporter.poster swallows its errors).
public struct APIClient: Sendable {
    public let baseURL: URL
    let transport: any HTTPTransport

    public init(baseURL: URL, transport: any HTTPTransport = URLSessionTransport()) {
        self.baseURL = baseURL
        self.transport = transport
    }

    public static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            guard let date = Timestamp.parse(text) else {
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "not an ISO 8601 date-time")
            }
            return date
        }
        return decoder
    }()

    public func version() async throws -> ServerVersion {
        try await get("version")
    }

    public func health() async throws -> Health {
        try await get("health")
    }

    /// The lineup for a year (0 = All Years) on today's date, or on `date` ("MM-DD").
    public func channels(year: Int, date: String? = nil) async throws -> Lineup {
        var query = [URLQueryItem(name: "year", value: String(year))]
        if let date { query.append(URLQueryItem(name: "date", value: date)) }
        return try await get("channels", query: query)
    }

    /// Every channel on the dial with its slots for `hours` from `start` ("HH:MM") on `date` ("MM-DD").
    public func guide(year: Int, date: String, start: String, hours: Double) async throws -> Guide {
        try await get("guide", query: [
            URLQueryItem(name: "year", value: String(year)),
            URLQueryItem(name: "date", value: date),
            URLQueryItem(name: "start", value: start),
            URLQueryItem(name: "hours", value: Self.number(hours)),
        ])
    }

    /// What is on a channel now, and the `count` slots after it.
    public func tune(channel: String, year: Int, count: Int = 3) async throws -> Tune {
        try await get(["tune", channel], query: [
            URLQueryItem(name: "year", value: String(year)),
            URLQueryItem(name: "count", value: String(count)),
        ])
    }

    /// `POST /api/watch`; the server answers 204.
    public func postWatch(_ report: WatchReport) async throws {
        var request = URLRequest(url: url(["watch"], query: []))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(report)
        _ = try await send(request, expecting: 204)
    }

    /// A signed media path made absolute against the server. Never log the result.
    public func mediaURL(_ path: SignedPath) -> URL? {
        URL(string: path.raw, relativeTo: baseURL)?.absoluteURL
    }

    // MARK: - Plumbing

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        try await get([path], query: query)
    }

    private func get<T: Decodable>(_ segments: [String], query: [URLQueryItem]) async throws -> T {
        var request = URLRequest(url: url(segments, query: query))
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let data = try await send(request, expecting: 200)
        do {
            return try Self.decoder.decode(T.self, from: data)
        } catch {
            throw APIError.badResponse
        }
    }

    private func send(_ request: URLRequest, expecting status: Int) async throws -> Data {
        var request = request
        request.cachePolicy = .reloadIgnoringLocalCacheData
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await transport.data(for: request)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw APIError.unreachable
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }
        guard http.statusCode == status else { throw APIError.status(http.statusCode) }
        return data
    }

    private func url(_ segments: [String], query: [URLQueryItem]) -> URL {
        // Percent-encode each path segment ourselves, so a "/" in an id can't add a segment.
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove("/")
        let path = segments.map { $0.addingPercentEncoding(withAllowedCharacters: allowed) ?? $0 }.joined(separator: "/")
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) ?? URLComponents()
        let basePath = components.percentEncodedPath.hasSuffix("/")
            ? String(components.percentEncodedPath.dropLast()) : components.percentEncodedPath
        components.percentEncodedPath = "\(basePath)/api/\(path)"
        components.queryItems = query.isEmpty ? nil : query
        return components.url ?? baseURL
    }

    /// 13.5 -> "13.5", 24 -> "24".
    static func number(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(value)
    }
}

/// Where the server is: Info.plist `RCServerURL` (https://tv.home.tabaska.us), overridden for tests
/// by the `-RCServerURL <url>` launch argument or the `RC_SERVER_URL` environment variable.
public enum ServerConfig {
    public static let defaultURL = URL(string: "https://tv.home.tabaska.us")!
    public static let infoKey = "RCServerURL"

    public static func resolve(info: String?, arguments: [String], environment: [String: String]) -> URL {
        var candidates: [String?] = []
        if let index = arguments.firstIndex(of: "-\(infoKey)"), index + 1 < arguments.count {
            candidates.append(arguments[index + 1])
        }
        candidates.append(environment["RC_SERVER_URL"])
        candidates.append(info)
        for candidate in candidates.compactMap({ $0 }) {
            if let url = parse(candidate) { return url }
        }
        return defaultURL
    }

    /// The app's server, from the main bundle and process.
    public static func current(bundle: Bundle = .main, process: ProcessInfo = .processInfo) -> URL {
        resolve(
            info: bundle.object(forInfoDictionaryKey: infoKey) as? String,
            arguments: process.arguments,
            environment: process.environment
        )
    }

    static func parse(_ text: String) -> URL? {
        var trimmed = text.trimmingCharacters(in: .whitespaces)
        while trimmed.hasSuffix("/") { trimmed.removeLast() }
        guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased(),
              scheme == "https" || scheme == "http", url.host != nil
        else { return nil }
        return url
    }
}
