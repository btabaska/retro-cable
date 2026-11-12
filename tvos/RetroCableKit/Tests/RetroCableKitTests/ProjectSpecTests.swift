import Foundation
import Testing

/// TV-1 / TV-2: what the XcodeGen spec promises for the sideload build.
@Suite struct ProjectSpecTests {
    static var project: String {
        (try? String(decoding: RepoFiles.data("tvos/project.yml"), as: UTF8.self)) ?? ""
    }

    @Test func buildsUsTabaskaRetrocableForTVOS18() {
        #expect(Self.project.contains("PRODUCT_BUNDLE_IDENTIFIER: us.tabaska.retrocable\n"))
        #expect(Self.project.contains("tvOS: \"18.0\""))
    }

    /// The committed team is empty; Brandon's Personal Team goes in the gitignored Local.xcconfig,
    /// which Signing.xcconfig includes, so it survives every `make tvos`.
    @Test func keepsTheTeamOutOfTheRepoAndAcrossRegeneration() throws {
        #expect(!Self.project.contains("DEVELOPMENT_TEAM:"))
        #expect(Self.project.contains("configFiles:\n  Debug: Signing.xcconfig\n  Release: Signing.xcconfig\n"))
        let signing = String(decoding: try RepoFiles.data("tvos/Signing.xcconfig"), as: UTF8.self)
        let lines = signing.split(separator: "\n").filter { !$0.hasPrefix("//") }
        #expect(lines == ["DEVELOPMENT_TEAM =", "#include? \"Local.xcconfig\""])
        let ignore = String(decoding: try RepoFiles.data(".gitignore"), as: UTF8.self)
        #expect(ignore.contains("\ntvos/Local.xcconfig\n"))
    }

    /// TV-2: the home screen shows the two-layer RC icon, at every size the brand assets need.
    @Test func hasTheAppIconAndTopShelfImage() throws {
        #expect(Self.project.contains("ASSETCATALOG_COMPILER_APPICON_NAME: App Icon & Top Shelf Image\n"))
        let set = "tvos/RetroCable/Resources/Assets.xcassets/App Icon & Top Shelf Image.brandassets"
        let assets = try #require(try RepoFiles.json("\(set)/Contents.json")["assets"] as? [[String: String]])
        #expect(Set(assets.map { "\($0["role"]!) \($0["size"]!)" }) == [
            "primary-app-icon 400x240", "primary-app-icon 1280x768",
            "top-shelf-image 1920x720", "top-shelf-image-wide 2320x720",
        ])
        let expected: [String: (Int, Int)] = [
            "App Icon.imagestack/Front.imagestacklayer/Content.imageset/front.png": (400, 240),
            "App Icon.imagestack/Front.imagestacklayer/Content.imageset/front@2x.png": (800, 480),
            "App Icon.imagestack/Back.imagestacklayer/Content.imageset/back.png": (400, 240),
            "App Icon.imagestack/Back.imagestacklayer/Content.imageset/back@2x.png": (800, 480),
            "App Icon - App Store.imagestack/Front.imagestacklayer/Content.imageset/front.png": (1280, 768),
            "App Icon - App Store.imagestack/Back.imagestacklayer/Content.imageset/back.png": (1280, 768),
            "Top Shelf Image.imageset/shelf.png": (1920, 720),
            "Top Shelf Image.imageset/shelf@2x.png": (3840, 1440),
            "Top Shelf Image Wide.imageset/shelf.png": (2320, 720),
            "Top Shelf Image Wide.imageset/shelf@2x.png": (4640, 1440),
        ]
        for (path, size) in expected {
            let png = try RepoFiles.data("\(set)/\(path)")
            let found = Self.pngSize(png)
            #expect(found?.0 == size.0 && found?.1 == size.1, "\(path)")
        }
        // Two layers in each icon stack, front first.
        for stack in ["App Icon.imagestack", "App Icon - App Store.imagestack"] {
            let layers = try RepoFiles.json("\(set)/\(stack)/Contents.json")["layers"] as? [[String: String]]
            #expect(layers?.map { $0["filename"] } == ["Front.imagestacklayer", "Back.imagestacklayer"])
        }
    }

    /// Width and height from a PNG's IHDR chunk.
    static func pngSize(_ data: Data) -> (Int, Int)? {
        guard data.count >= 24, data.prefix(8) == Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) else { return nil }
        let bytes = [UInt8](data[16..<24])
        let width = bytes[0..<4].reduce(0) { $0 << 8 | Int($1) }
        let height = bytes[4..<8].reduce(0) { $0 << 8 | Int($1) }
        return (width, height)
    }

    @Test func pointsAtTheHomelabWithoutATSExceptions() {
        #expect(Self.project.contains("RCServerURL: https://tv.home.tabaska.us"))
        #expect(!Self.project.contains("NSAppTransportSecurity"))
        #expect(!Self.project.contains("NSAllowsArbitraryLoads"))
    }

    /// The server is a LAN host; if tvOS asks for local network access, it needs a reason.
    @Test func explainsLocalNetworkUse() {
        #expect(Self.project.contains("NSLocalNetworkUsageDescription:"))
    }

    @Test func generatedFilesAreIgnored() throws {
        let ignore = String(decoding: try RepoFiles.data(".gitignore"), as: UTF8.self)
        #expect(ignore.contains("tvos/RetroCable.xcodeproj/"))
        #expect(ignore.contains("tvos/build/"))
    }
}
