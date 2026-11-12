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

    /// Brandon picks his Personal Team in Xcode.
    @Test func leavesTheTeamEmpty() {
        #expect(Self.project.contains("DEVELOPMENT_TEAM: \"\""))
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
