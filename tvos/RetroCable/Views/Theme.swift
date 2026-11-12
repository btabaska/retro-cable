import os
import RetroCableKit
import SwiftUI
import UIKit

/// Token colors as SwiftUI colors. Views never write a hex value (D-2).
enum Palette {
    static let background = Color(rc: DesignTokens.color.background)
    static let header = Color(rc: DesignTokens.color.header)
    static let rule = Color(rc: DesignTokens.color.rule)
    static let focus = Color(rc: DesignTokens.color.focus)
    static let onFocus = Color(rc: DesignTokens.color.onFocus)
    static let text = Color(rc: DesignTokens.color.text)
    static let textMuted = Color(rc: DesignTokens.color.textMuted)
    static let textDim = Color(rc: DesignTokens.color.textDim)
    static let glow = Color(rc: DesignTokens.color.glow)
    static let black = Color(rc: DesignTokens.color.black)
}

/// The bundled faces at fixed sizes (never scaled by Dynamic Type). If a face failed to register,
/// fall back to SF Pro (body) or SF Pro Condensed (grid) and log it once.
@MainActor
enum RCFont {
    static func body(_ size: CGFloat, bold: Bool = false, italic: Bool = false) -> Font {
        font(FontFace.face(family: DesignTokens.font.body, bold: bold, italic: italic), size: size) {
            let base = Font.system(size: size, weight: bold ? .bold : .regular)
            return italic ? base.italic() : base
        }
    }

    static func grid(_ size: CGFloat, bold: Bool = false) -> Font {
        font(FontFace.face(family: DesignTokens.font.grid, bold: bold), size: size) {
            Font.system(size: size, weight: bold ? .bold : .regular).width(.condensed)
        }
    }

    private static func font(_ face: FontFace, size: CGFloat, fallback: () -> Font) -> Font {
        if registered(face) { return Font.custom(face.postScriptName, fixedSize: size) }
        return fallback()
    }

    private static var checked: [FontFace: Bool] = [:]

    private static func registered(_ face: FontFace) -> Bool {
        if let known = checked[face] { return known }
        let ok = UIFont(name: face.postScriptName, size: 12) != nil
        if !ok { Logger.app.error("font \(face.postScriptName, privacy: .public) did not register; using the system font") }
        checked[face] = ok
        return ok
    }
}

extension Logger {
    /// Never log a media URL, its exp or sig, or any token (TV-41).
    static let app = Logger(subsystem: "us.tabaska.retrocable", category: "app")
}
