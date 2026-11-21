/// The five bundled faces (docs/design/tvos.md section 3), OFL 1.1, from pinned upstream commits.
/// The app registers them with UIAppFonts and draws every string at a fixed size.
public enum FontFace: String, CaseIterable, Sendable {
    case arimoRegular = "Arimo-Regular"
    case arimoBold = "Arimo-Bold"
    case arimoItalic = "Arimo-Italic"
    case archivoNarrowRegular = "ArchivoNarrow-Regular"
    case archivoNarrowBold = "ArchivoNarrow-Bold"

    /// The PostScript name, for `Font.custom` and `UIFont(name:size:)`.
    public var postScriptName: String { rawValue }

    /// The file in the app bundle (tvos/RetroCable/Resources/Fonts/).
    public var fileName: String { "\(rawValue).ttf" }

    /// SHA-256 of the pinned upstream TTF.
    public var sha256: String {
        switch self {
        case .arimoRegular: "41b22bc8f0b51f932825d37bc55b5eb6ba67dfe599a626e4aff2b43b624f9f8c"
        case .arimoBold: "d7a8b187cf8444d4cfee102e8eae9e3043682fd5106d5d33ed677fe268a0e2ba"
        case .arimoItalic: "ffdf690e4357a392e4edcc3df0b3adb0302920f75e54f2e6eec355afbe7edcba"
        case .archivoNarrowRegular: "8b2f285c60e450933c7deb3ad5acba00e5b34fb1b36f07490ab23d3f5e6df9e0"
        case .archivoNarrowBold: "e1b016241fd2bf89796152d7875464ab0be7181160caa8a794b77cb94493fc1d"
        }
    }

    /// `font.body` (Arimo) or `font.grid` (Archivo Narrow).
    public static func face(family: String, bold: Bool = false, italic: Bool = false) -> FontFace {
        if family == DesignTokens.font.grid { return bold ? .archivoNarrowBold : .archivoNarrowRegular }
        if italic { return .arimoItalic }
        return bold ? .arimoBold : .arimoRegular
    }
}
