import Foundation

/// Files the tests read straight from the repo: the API contract, the design tokens and the
/// recorded-shape JSON fixtures next to this file.
enum RepoFiles {
    /// The repo root: the first folder above this file that holds `server/openapi.json`.
    static let root: URL = {
        var url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        while url.path != "/" {
            if FileManager.default.fileExists(atPath: url.appendingPathComponent("server/openapi.json").path) {
                return url
            }
            url.deleteLastPathComponent()
        }
        fatalError("repo root not found above \(#filePath)")
    }()

    static func data(_ relativePath: String) throws -> Data {
        try Data(contentsOf: root.appendingPathComponent(relativePath))
    }

    static func json(_ relativePath: String) throws -> [String: Any] {
        let object = try JSONSerialization.jsonObject(with: data(relativePath))
        guard let dict = object as? [String: Any] else { throw FixtureError.notAnObject(relativePath) }
        return dict
    }

    static func fixture(_ name: String) throws -> Data {
        try data("tvos/RetroCableKit/Tests/RetroCableKitTests/Fixtures/\(name)")
    }
}

enum FixtureError: Error {
    case notAnObject(String)
}
