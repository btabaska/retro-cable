import CoreGraphics
import SwiftUI

// design/tokens.json as Swift. Hand-maintained: DesignTokensTests fails when a key or value here
// and in the JSON differ in either direction, or when the JSON grows a group nobody decided on.
// One stored property per token, same name, so the test can read them with Mirror.

/// `color.*` as 0xRRGGBB.
public struct ColorTokens: Sendable {
    public let background: UInt32 = 0x0B1E4A
    public let header: UInt32 = 0x06112E
    public let rule: UInt32 = 0x2E5AAC
    public let channel: UInt32 = 0x102A63
    public let cell: UInt32 = 0x1C3A7A
    public let cellPast: UInt32 = 0x16306A
    public let focus: UInt32 = 0xF2C230
    public let onFocus: UInt32 = 0x0B1E4A
    public let text: UInt32 = 0xFFFFFF
    public let textMuted: UInt32 = 0xB9C8EA
    public let textDim: UInt32 = 0x8FA3D1
    public let holiday: UInt32 = 0xFF9F1C
    public let holidayMuted: UInt32 = 0xE0A458
    public let transplant: UInt32 = 0x7FD1FF
    public let black: UInt32 = 0x000000
    public let glow: UInt32 = 0x13306E
    public let track: UInt32 = 0x2A3F75
    public let divider: UInt32 = 0x1B2F5E
    public let digits: UInt32 = 0x7CFF6B
    public let filler: UInt32 = 0x101010
}

/// `font.*`: the first family of each CSS stack.
public struct FontTokens: Sendable {
    public let body = "Arimo"
    public let grid = "Archivo Narrow"
}

/// `fontSize.*` in points ("16px" -> 16).
public struct FontSizeTokens: Sendable {
    public let caption: CGFloat = 16
    public let small: CGFloat = 22
    public let body: CGFloat = 26
    public let label: CGFloat = 30
    public let clock: CGFloat = 36
    public let heading: CGFloat = 44
    public let title: CGFloat = 60
}

/// `space.*` in points.
public struct SpaceTokens: Sendable {
    public let cellGap: CGFloat = 4
    public let rowGap: CGFloat = 6
    public let cellPad: CGFloat = 12
    public let gutter: CGFloat = 64
}

/// `radius.*` in points.
public struct RadiusTokens: Sendable {
    public let button: CGFloat = 6
    public let badge: CGFloat = 4
}

/// `screen.*`: the 1920 x 1080 stage, which is also tvOS's point canvas.
public struct ScreenTokens: Sendable {
    public let width: CGFloat = 1920
    public let height: CGFloat = 1080
}

public enum DesignTokens {
    public static let color = ColorTokens()
    public static let font = FontTokens()
    public static let fontSize = FontSizeTokens()
    public static let space = SpaceTokens()
    public static let radius = RadiusTokens()
    public static let screen = ScreenTokens()
}

/// 0xRRGGBB split into sRGB components, 0...1.
public struct RGB: Equatable, Sendable {
    public let red: Double
    public let green: Double
    public let blue: Double

    public init(hex: UInt32) {
        red = Double((hex >> 16) & 0xFF) / 255
        green = Double((hex >> 8) & 0xFF) / 255
        blue = Double(hex & 0xFF) / 255
    }
}

extension Color {
    /// A token color: `Color(rc: DesignTokens.color.focus)`. sRGB, opaque.
    public init(rc hex: UInt32) {
        let rgb = RGB(hex: hex)
        self.init(.sRGB, red: rgb.red, green: rgb.green, blue: rgb.blue, opacity: 1)
    }
}
