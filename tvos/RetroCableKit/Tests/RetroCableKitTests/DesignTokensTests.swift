import CoreGraphics
import Foundation
import SwiftUI
import Testing
@testable import RetroCableKit

/// D-1 / TV-44: DesignTokens.swift is design/tokens.json, key for key and value for value.
@Suite struct DesignTokensTests {
    static var tokens: [String: Any] {
        do { return try RepoFiles.json("design/tokens.json") } catch { fatalError("tokens.json: \(error)") }
    }

    /// Groups the browser uses and tvOS deliberately doesn't. A new group fails until it is decided.
    static let skippedGroups: Set<String> = ["admin"]

    static func swiftGroup(_ name: String) -> Any? {
        switch name {
        case "color": DesignTokens.color
        case "fontSize": DesignTokens.fontSize
        case "space": DesignTokens.space
        case "radius": DesignTokens.radius
        case "screen": DesignTokens.screen
        case "font": DesignTokens.font
        default: nil
        }
    }

    static func properties(of value: Any) -> [String: Any] {
        var out: [String: Any] = [:]
        for child in Mirror(reflecting: value).children {
            if let label = child.label { out[label] = child.value }
        }
        return out
    }

    @Test func everyGroupIsDecided() {
        let groups = Set(Self.tokens.keys.filter { !$0.hasPrefix("$") })
        let handled: Set<String> = ["color", "fontSize", "space", "radius", "screen", "font"]
        #expect(groups.subtracting(handled) == Self.skippedGroups, "new token group: decide whether tvOS needs it")
        #expect(handled.isSubset(of: groups))
    }

    @Test(arguments: ["color", "fontSize", "space", "radius", "screen", "font"])
    func groupMatchesTheJSON(group: String) throws {
        let json = try #require(Self.tokens[group] as? [String: Any])
        let jsonKeys = Set(json.keys.filter { !$0.hasPrefix("$") })
        let swift = Self.properties(of: try #require(Self.swiftGroup(group)))
        #expect(
            Set(swift.keys) == jsonKeys,
            "\(group): JSON only \(jsonKeys.subtracting(swift.keys).sorted()), Swift only \(Set(swift.keys).subtracting(jsonKeys).sorted())"
        )
        for key in jsonKeys {
            let raw = json[key]
            let value = swift[key]
            switch group {
            case "color":
                let hex = try #require(raw as? String)
                #expect(value as? UInt32 == UInt32(hex.dropFirst(), radix: 16), "color.\(key)")
            case "font":
                let stack = try #require(raw as? String)
                let first = stack.split(separator: ",").first.map {
                    $0.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "'\""))
                }
                #expect(value as? String == first, "font.\(key)")
            default:
                let number: Double
                if let string = raw as? String {
                    number = try #require(Double(string.replacingOccurrences(of: "px", with: "")))
                } else {
                    number = try #require((raw as? NSNumber)?.doubleValue)
                }
                #expect((value as? CGFloat).map(Double.init) == number, "\(group).\(key)")
            }
        }
    }

    @Test func rgbFromHex() {
        let gold = RGB(hex: 0xF2C230)
        #expect(gold.red == 0xF2 / 255.0)
        #expect(gold.green == 0xC2 / 255.0)
        #expect(gold.blue == 0x30 / 255.0)
    }

    @Test func colorFromATokenIsSRGB() throws {
        let resolved = Color(rc: DesignTokens.color.focus).resolve(in: EnvironmentValues())
        let components = try #require(resolved.cgColor.converted(
            to: CGColorSpace(name: CGColorSpace.sRGB)!, intent: .defaultIntent, options: nil
        )?.components)
        #expect(abs(Double(components[0]) - 0xF2 / 255.0) < 0.002)
        #expect(abs(Double(components[1]) - 0xC2 / 255.0) < 0.002)
        #expect(abs(Double(components[2]) - 0x30 / 255.0) < 0.002)
        #expect(abs(Double(components[3]) - 1) < 0.0001)
    }

    /// The system launch screen is the `header` token, so launch flows into power on (D-11).
    @Test func launchScreenColorIsTheHeaderToken() throws {
        let path = "tvos/RetroCable/Resources/Assets.xcassets/LaunchBackground.colorset/Contents.json"
        let json = try RepoFiles.json(path)
        let colors = try #require(json["colors"] as? [[String: Any]])
        let components = try #require((colors.first?["color"] as? [String: Any])?["components"] as? [String: String])
        func byte(_ key: String) -> UInt32 { UInt32(components[key]!.dropFirst(2), radix: 16) ?? 999 }
        #expect((byte("red") << 16 | byte("green") << 8 | byte("blue")) == DesignTokens.color.header)
    }
}
