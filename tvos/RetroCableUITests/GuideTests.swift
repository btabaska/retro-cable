import XCTest

/// The guide and the year picker with the Siri Remote (TV-22 to TV-31) against the server the app
/// points at. Expects the homelab's 1998 dial (02 KTVU, 04 KRON, 05 KPIX, ...); see HANDOFF.md.
@MainActor
final class GuideTests: RCUITestCase {
    func label(_ app: XCUIApplication, _ id: String) -> String {
        let found = element(app, id)
        return found.exists ? found.label : ""
    }

    /// Live TV, then Select: the guide, loaded.
    func inTheGuide(year: String = "1998") -> XCUIApplication {
        let app = turnedOn(year: year)
        remote.press(.select)
        XCTAssertTrue(element(app, "guide").waitForExistence(timeout: 5))
        XCTAssertTrue(waitFor(self.element(app, "guide-row-ktvu").exists, timeout: 10), app.debugDescription)
        XCTAssertTrue(waitFor(self.label(app, "details-title") != "Loading the guide…", timeout: 10))
        return app
    }

    /// TV-22 to TV-24, TV-28: the guide opens on the tuned channel's program now with the details
    /// panel; arrows move the focus and the details follow; Select tunes the focused row.
    func testGuideOpensOnTheTunedChannelAndSelectTunesTheFocusedRow() {
        let app = inTheGuide()
        XCTAssertTrue(element(app, "guide-details").exists)
        XCTAssertEqual(label(app, "year-badge"), "1998")
        XCTAssertTrue(element(app, "guide-row-nbc").exists)
        XCTAssertNotEqual(label(app, "details-title"), "No signal")
        // Let the PiP draw a frame for the screenshot.
        RunLoop.current.run(until: Date().addingTimeInterval(2))
        snap("tv-guide")
        remote.press(.down)
        remote.press(.right)
        remote.press(.right)
        XCTAssertTrue(waitFor(self.element(app, "details-title").exists, timeout: 2))
        RunLoop.current.run(until: Date().addingTimeInterval(1))
        snap("tv-guide-moved")
        remote.press(.select)
        XCTAssertTrue(element(app, "live").waitForExistence(timeout: 5))
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 5))
        XCTAssertTrue(waitFor(self.number(app) == "04"))
        XCTAssertFalse(element(app, "guide").exists)
    }

    /// TV-25: the window scrolls by half hours and comes back; left at the start does nothing.
    func testLeftAndRightScrollTheWindow() {
        let app = inTheGuide()
        for _ in 0..<8 { remote.press(.right) }
        RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        XCTAssertTrue(element(app, "guide").exists)
        for _ in 0..<12 { remote.press(.left) }
        RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        XCTAssertTrue(element(app, "guide").exists)
        XCTAssertEqual(app.state, .runningForeground)
    }

    /// TV-29 to TV-31: Play/Pause opens the year picker on the current year with its date and dial;
    /// Back returns to the guide; Select sets the year and returns to Live TV.
    func testYearPickerSetsTheYear() {
        let app = inTheGuide()
        remote.press(.playPause)
        XCTAssertTrue(element(app, "year-picker").waitForExistence(timeout: 5))
        XCTAssertFalse(element(app, "guide").exists)
        XCTAssertTrue(waitFor(self.label(app, "year-date").hasSuffix("1998"), timeout: 10))
        XCTAssertTrue(waitFor(self.label(app, "year-date").contains(","), timeout: 10), label(app, "year-date"))
        XCTAssertTrue(element(app, "year-dial").exists)
        RunLoop.current.run(until: Date().addingTimeInterval(1))
        snap("tv-year-picker")
        remote.press(.menu)
        XCTAssertTrue(element(app, "guide").waitForExistence(timeout: 5))
        XCTAssertFalse(element(app, "year-picker").exists)
        remote.press(.playPause)
        XCTAssertTrue(element(app, "year-picker").waitForExistence(timeout: 5))
        remote.press(.right)
        XCTAssertTrue(waitFor(self.label(app, "year-date").hasSuffix("1999"), timeout: 10), label(app, "year-date"))
        remote.press(.select)
        XCTAssertTrue(element(app, "live").waitForExistence(timeout: 5))
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 5))
        XCTAssertFalse(element(app, "year-picker").exists)
        remote.press(.select)
        XCTAssertTrue(element(app, "guide").waitForExistence(timeout: 5))
        XCTAssertTrue(waitFor(self.label(app, "year-badge") == "1999"))
    }

    /// All Years sits alone below the grid: down three times from 1998.
    func testAllYearsIsBelowTheGrid() {
        let app = inTheGuide()
        remote.press(.playPause)
        XCTAssertTrue(element(app, "year-picker").waitForExistence(timeout: 5))
        for _ in 0..<3 { remote.press(.down) }
        XCTAssertTrue(waitFor(self.element(app, "year-0").isSelected, timeout: 3))
        RunLoop.current.run(until: Date().addingTimeInterval(1.5))
        snap("tv-year-picker-all-years")
        remote.press(.up)
        XCTAssertTrue(waitFor(self.element(app, "year-2004").isSelected, timeout: 3))
    }
}
