import CryptoKit
import Foundation
import Testing
@testable import RetroCableKit

/// D-3: the five pinned TTFs and their licenses are in the app, and Info.plist registers them.
/// (The app-target FontTests prove they load on tvOS once the simulator exists.)
@Suite struct FontFaceTests {
    static let folder = "tvos/RetroCable/Resources/Fonts"

    @Test(arguments: FontFace.allCases)
    func bundledFileMatchesThePinnedHash(face: FontFace) throws {
        let data = try RepoFiles.data("\(Self.folder)/\(face.fileName)")
        let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        #expect(hash == face.sha256)
    }

    @Test func licensesShipWithTheFonts() throws {
        for name in ["OFL-Arimo.txt", "OFL-ArchivoNarrow.txt"] {
            let text = try String(decoding: RepoFiles.data("\(Self.folder)/\(name)"), as: UTF8.self)
            #expect(text.contains("SIL Open Font License, Version 1.1"))
        }
    }

    @Test func projectRegistersEveryFace() throws {
        let project = try String(decoding: RepoFiles.data("tvos/project.yml"), as: UTF8.self)
        #expect(project.contains("UIAppFonts"))
        for face in FontFace.allCases {
            #expect(project.contains(face.fileName), "\(face.fileName) not in UIAppFonts")
        }
    }

    @Test func picksTheFaceForAFamily() {
        #expect(FontFace.face(family: DesignTokens.font.grid, bold: true) == .archivoNarrowBold)
        #expect(FontFace.face(family: DesignTokens.font.grid) == .archivoNarrowRegular)
        #expect(FontFace.face(family: DesignTokens.font.body, bold: true) == .arimoBold)
        #expect(FontFace.face(family: DesignTokens.font.body, italic: true) == .arimoItalic)
        #expect(FontFace.face(family: DesignTokens.font.body) == .arimoRegular)
    }
}
