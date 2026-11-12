import Foundation
import Testing
@testable import RetroCableKit

/// TV-32 / TV-33: rc.year and rc.channel in UserDefaults.
@Suite struct SettingsTests {
    func fresh() -> UserDefaults {
        let name = "rc-tests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        return defaults
    }

    @Test func missingOrInvalidYearMeans1998() {
        let defaults = fresh()
        #expect(Settings(defaults).year == 1998)
        defaults.set("1989", forKey: "rc.year")
        #expect(Settings(defaults).year == 1998)
        defaults.set(2011, forKey: "rc.year")
        #expect(Settings(defaults).year == 1998)
        defaults.set("junk", forKey: "rc.year")
        #expect(Settings(defaults).year == 1998)
    }

    @Test func storedZeroIsAllYears() {
        let defaults = fresh()
        defaults.set(0, forKey: "rc.year")
        #expect(Settings(defaults).year == 0)
        defaults.set("0", forKey: "rc.year")
        #expect(Settings(defaults).year == 0)
    }

    @Test func savesAndRestoresYearAndChannel() {
        let defaults = fresh()
        let settings = Settings(defaults)
        #expect(settings.channel == nil)
        settings.year = 2005
        settings.channel = "adult-swim"
        let again = Settings(defaults)
        #expect(again.year == 2005)
        #expect(again.channel == "adult-swim")
        again.year = 0
        #expect(Settings(defaults).year == 0)
    }

    @Test func refusesAnInvalidYear() {
        let defaults = fresh()
        let settings = Settings(defaults)
        settings.year = 1975
        #expect(Settings(defaults).year == 1998)
    }
}
