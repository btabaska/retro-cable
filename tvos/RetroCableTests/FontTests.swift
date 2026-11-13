import RetroCableKit
import Testing
import UIKit

/// D-3: all five bundled faces register through UIAppFonts and load by PostScript name.
@Suite struct FontTests {
    @Test(arguments: FontFace.allCases)
    func faceLoads(face: FontFace) {
        #expect(UIFont(name: face.postScriptName, size: 10) != nil, "\(face.postScriptName) did not load")
    }
}
