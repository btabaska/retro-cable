import Foundation
import Testing
@testable import RetroCableKit

/// The Swift models against `server/openapi.json`. For each TV schema the test builds a sample
/// object from the schema itself, decodes it into the model and encodes it back: the keys must
/// match the schema's properties exactly. So a field the server adds (required or not), drops or
/// renames fails here until the model follows. A second pass decodes a sample that has only the
/// required fields, with every nullable one null, so the models never demand more than the
/// contract promises.
@Suite struct ContractTests {
    static var openAPI: [String: Any] {
        do { return try RepoFiles.json("server/openapi.json") } catch { fatalError("openapi.json: \(error)") }
    }

    static var schemas: [String: Any] {
        ((openAPI["components"] as? [String: Any])?["schemas"] as? [String: Any]) ?? [:]
    }

    /// Every schema the app decodes or sends, with a decode-then-encode round trip.
    static var models: [String: (Data) throws -> Data] { [
        "ChannelInfo": roundTrip(ChannelInfo.self),
        "Lineup": roundTrip(Lineup.self),
        "GuideSlot": roundTrip(GuideSlot.self),
        "GuideRow": roundTrip(GuideRow.self),
        "Guide": roundTrip(Guide.self),
        "TunedSlot": roundTrip(TunedSlot.self),
        "Tune": roundTrip(Tune.self),
        "WatchReport": roundTrip(WatchReport.self),
        "Version": roundTrip(ServerVersion.self),
        "Health": roundTrip(Health.self),
    ] }

    static func roundTrip<T: Codable>(_ type: T.Type) -> (Data) throws -> Data {
        { data in try JSONEncoder().encode(try APIClient.decoder.decode(T.self, from: data)) }
    }

    @Test(arguments: models.keys.sorted())
    func modelKeysMatchTheSchema(name: String) throws {
        let schema = try #require(Self.schemas[name] as? [String: Any], "\(name) is not in openapi.json")
        let properties = Set((schema["properties"] as? [String: Any] ?? [:]).keys)
        let sample = Sampler(schemas: Self.schemas).sample(schema, full: true)
        let data = try JSONSerialization.data(withJSONObject: sample)
        let encoded = try #require(try Self.models[name]?(data))
        let object = try #require(try JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        let keys = Set(object.keys)
        #expect(
            keys == properties,
            "\(name): schema has \(properties.subtracting(keys).sorted()) the model lacks; model has \(keys.subtracting(properties).sorted()) the schema lacks"
        )
        let required = Set(schema["required"] as? [String] ?? [])
        #expect(required.isSubset(of: keys), "\(name): required \(required.subtracting(keys).sorted()) missing from the model")
    }

    @Test(arguments: models.keys.sorted())
    func modelsDecodeTheLeastTheContractPromises(name: String) throws {
        let schema = try #require(Self.schemas[name] as? [String: Any])
        let sample = Sampler(schemas: Self.schemas).sample(schema, full: false)
        let data = try JSONSerialization.data(withJSONObject: sample)
        _ = try #require(try Self.models[name]?(data))
    }

    /// The calls the client makes exist with the parameters and bodies it uses.
    @Test func endpointsTheClientCallsExist() throws {
        let paths = try #require(Self.openAPI["paths"] as? [String: Any])
        func operation(_ path: String, _ method: String) throws -> [String: Any] {
            let item = try #require(paths[path] as? [String: Any], "\(path) missing")
            return try #require(item[method] as? [String: Any], "\(method) \(path) missing")
        }
        func params(_ op: [String: Any]) -> Set<String> {
            Set((op["parameters"] as? [[String: Any]] ?? []).compactMap { $0["name"] as? String })
        }
        func okRef(_ op: [String: Any]) -> String? {
            let responses = op["responses"] as? [String: Any]
            let ok = responses?["200"] as? [String: Any]
            let content = (ok?["content"] as? [String: Any])?["application/json"] as? [String: Any]
            return (content?["schema"] as? [String: Any])?["$ref"] as? String
        }
        let channels = try operation("/channels", "get")
        #expect(params(channels).isSuperset(of: ["year", "date"]))
        #expect(okRef(channels) == "#/components/schemas/Lineup")
        let guide = try operation("/guide", "get")
        #expect(params(guide).isSuperset(of: ["year", "date", "start", "hours"]))
        #expect(okRef(guide) == "#/components/schemas/Guide")
        let tune = try operation("/tune/{channel_id}", "get")
        #expect(params(tune).isSuperset(of: ["channel_id", "year", "count"]))
        #expect(okRef(tune) == "#/components/schemas/Tune")
        #expect(okRef(try operation("/version", "get")) == "#/components/schemas/Version")
        #expect(okRef(try operation("/health", "get")) == "#/components/schemas/Health")
        let watch = try operation("/watch", "post")
        let body = ((watch["requestBody"] as? [String: Any])?["content"] as? [String: Any])?["application/json"]
        #expect(((body as? [String: Any])?["schema"] as? [String: Any])?["$ref"] as? String == "#/components/schemas/WatchReport")
        #expect((watch["responses"] as? [String: Any])?["204"] != nil)
    }
}

/// Builds a JSON object that satisfies an OpenAPI schema.
struct Sampler {
    let schemas: [String: Any]

    func sample(_ schema: [String: Any], full: Bool) -> Any {
        if let ref = schema["$ref"] as? String {
            let name = String(ref.split(separator: "/").last ?? "")
            return sample(schemas[name] as? [String: Any] ?? [:], full: full)
        }
        if let options = schema["anyOf"] as? [[String: Any]] {
            let nullable = options.contains { $0["type"] as? String == "null" }
            if nullable && !full { return NSNull() }
            let first = options.first { $0["type"] as? String != "null" } ?? [:]
            return sample(first, full: full)
        }
        if let values = schema["enum"] as? [Any], let first = values.first { return first }
        switch schema["type"] as? String {
        case "object", nil:
            let properties = schema["properties"] as? [String: Any] ?? [:]
            let required = Set(schema["required"] as? [String] ?? [])
            var object: [String: Any] = [:]
            for (key, value) in properties where full || required.contains(key) {
                object[key] = sample(value as? [String: Any] ?? [:], full: full)
            }
            return object
        case "array":
            return [sample(schema["items"] as? [String: Any] ?? [:], full: full)]
        case "integer":
            return 3
        case "number":
            return 1.5
        case "boolean":
            return true
        default:
            switch schema["format"] as? String {
            case "date-time": return "1998-11-11T19:33:15-05:00"
            case "date": return "1998-11-11"
            default: return "sample-value"
            }
        }
    }
}
