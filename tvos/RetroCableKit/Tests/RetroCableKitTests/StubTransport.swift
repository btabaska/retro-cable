import Foundation
@testable import RetroCableKit

/// A transport that answers from a handler and records every request.
final class StubTransport: HTTPTransport, @unchecked Sendable {
    typealias Handler = @Sendable (URLRequest) throws -> (Int, Data)
    private let lock = NSLock()
    private var handler: Handler
    private var recorded: [URLRequest] = []

    init(_ handler: @escaping Handler) {
        self.handler = handler
    }

    /// Answers every request with one status and body.
    convenience init(status: Int = 200, body: Data = Data()) {
        self.init { _ in (status, body) }
    }

    var requests: [URLRequest] {
        lock.withLock { recorded }
    }

    func respond(_ handler: @escaping Handler) {
        lock.withLock { self.handler = handler }
    }

    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        let handler = lock.withLock {
            recorded.append(request)
            return self.handler
        }
        let (status, body) = try handler(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: nil)!
        return (body, response)
    }
}

struct Offline: Error {}

/// Answers by path: fixture bodies for the TV endpoints, or an error.
func fixtureTransport(failing: Bool = false) -> StubTransport {
    StubTransport { request in
        if failing { throw URLError(.cannotConnectToHost) }
        let path = request.url?.path ?? ""
        let name: String
        switch true {
        case path.hasSuffix("/api/version"): name = "version.json"
        case path.hasSuffix("/api/channels"): name = "channels.json"
        case path.hasSuffix("/api/guide"): name = "guide.json"
        case path.contains("/api/tune/"): name = "tune.json"
        default: return (404, Data())
        }
        return (200, try RepoFiles.fixture(name))
    }
}
