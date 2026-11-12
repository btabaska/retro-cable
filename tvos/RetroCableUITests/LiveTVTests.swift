import XCTest

/// Live TV with the Siri Remote (TV-4, TV-13, TV-15 to TV-18, TV-28) against the server the app
/// points at (the homelab by default; RC_SERVER_URL for the Docker test stack). Watch reports are
/// off (-RCNoWatch) so test runs never add viewing rows. Set RC_SCREENS to a folder to save
/// screenshots there (make test-tvos-ui saves them to tvos/build/screens).
@MainActor
final class LiveTVTests: RCUITestCase {
    /// TV-4, TV-18: turning on goes to Live TV on the stored channel with the banner and clock,
    /// and the banner leaves after 4 s.
    func testTurningOnShowsTheBanner() {
        let app = launch()
        snap("tv-power-on")
        remote.press(.select)
        XCTAssertTrue(element(app, "live").waitForExistence(timeout: 15))
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 5))
        XCTAssertTrue(element(app, "clock").exists)
        XCTAssertEqual(number(app), "02")
        XCTAssertTrue(waitFor(self.element(app, "banner-title").label != "Tuning…", timeout: 5))
        snap("tv-live-banner")
        XCTAssertTrue(waitFor(!self.element(app, "info-banner").exists, timeout: 6))
        XCTAssertFalse(element(app, "clock").exists)
        // Give the picture a moment, then keep a frame for the D-14 comparison.
        RunLoop.current.run(until: Date().addingTimeInterval(2))
        snap("tv-live")
    }

    /// TV-13, TV-15, TV-17: up/down change channel, Play/Pause is last channel, left/right and
    /// Back (while hidden) show the banner.
    func testChannelUpDownAndLastChannel() {
        let app = launch()
        remote.press(.select)
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 15))
        XCTAssertEqual(number(app), "02")
        remote.press(.up)
        XCTAssertTrue(waitFor(self.number(app) == "04"))
        remote.press(.down)
        XCTAssertTrue(waitFor(self.number(app) == "02"))
        remote.press(.down)
        let wrapped = number(app)
        XCTAssertTrue(waitFor(self.number(app) != "02"))
        XCTAssertGreaterThan(Int(number(app)) ?? 0, 9, "down from the lowest wraps to the highest, \(wrapped)")
        let top = number(app)
        remote.press(.playPause)
        XCTAssertTrue(waitFor(self.number(app) == "02"))
        remote.press(.playPause)
        XCTAssertTrue(waitFor(self.number(app) == top))
        XCTAssertTrue(waitFor(!self.element(app, "info-banner").exists, timeout: 6))
        remote.press(.right)
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 2))
        XCTAssertTrue(waitFor(!self.element(app, "info-banner").exists, timeout: 6))
        remote.press(.menu)
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 2))
        XCTAssertEqual(app.state, .runningForeground)
    }

    /// TV-14: five quick presses land five channels up.
    func testRapidFlippingLandsOnTheFifthChannel() {
        let app = launch()
        remote.press(.select)
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 15))
        for _ in 0..<5 { remote.press(.up) }
        // 02 -> 04 -> 05 -> 09 -> 20 -> 21 on the 1998 dial.
        XCTAssertTrue(waitFor(self.number(app) == "21"))
        XCTAssertTrue(waitFor(self.element(app, "banner-title").label != "Tuning…", timeout: 8))
    }

    /// TV-16, TV-28: holding Select does nothing; a click opens the guide, which shows the PiP;
    /// Back returns to Live TV.
    func testSelectOpensTheGuideAndHoldDoesNot() {
        let app = launch()
        remote.press(.select)
        XCTAssertTrue(element(app, "live").waitForExistence(timeout: 15))
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 5))
        remote.press(.select, forDuration: 1.0)
        RunLoop.current.run(until: Date().addingTimeInterval(1))
        XCTAssertFalse(element(app, "guide").exists, "holding Select must not open the guide")
        remote.press(.select)
        XCTAssertTrue(element(app, "guide").waitForExistence(timeout: 5))
        XCTAssertTrue(element(app, "player").exists)
        RunLoop.current.run(until: Date().addingTimeInterval(1.5))
        snap("tv-guide-pip")
        remote.press(.menu)
        XCTAssertTrue(element(app, "live").waitForExistence(timeout: 5))
        XCTAssertFalse(element(app, "guide").exists)
        XCTAssertEqual(app.state, .runningForeground)
    }

    /// TV-17: Back while the banner shows leaves the app.
    func testBackWithTheBannerShowingLeaves() {
        let app = launch()
        remote.press(.select)
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 15))
        remote.press(.menu)
        XCTAssertTrue(waitFor(app.state != .runningForeground, timeout: 5))
    }

    /// TV-37: Home sends the app away; coming back shows Live TV again with the banner.
    func testReturningFromTheBackgroundShowsLiveWithTheBanner() {
        let app = launch()
        remote.press(.select)
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 15))
        XCTAssertTrue(waitFor(!self.element(app, "info-banner").exists, timeout: 6))
        remote.press(.home)
        XCTAssertTrue(waitFor(app.state != .runningForeground, timeout: 5))
        RunLoop.current.run(until: Date().addingTimeInterval(2))
        app.activate()
        XCTAssertTrue(element(app, "live").waitForExistence(timeout: 10))
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 5))
        XCTAssertTrue(waitFor(self.element(app, "banner-title").label != "Tuning…", timeout: 8))
    }

    /// TV-40's measurement: 20 channel changes 4 s apart. Each one's press-to-first-frame time
    /// is in the log ("tune <channel> <ms> ms", subsystem us.tabaska.retrocable). Opt-in, since it
    /// takes a minute and a half: RC_FLIPS=1 make test-tvos-ui.
    func testTwentyFlipsForTuneTimes() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["RC_FLIPS"] == "1", "set RC_FLIPS=1 to measure")
        let app = launch()
        remote.press(.select)
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 15))
        for flip in 0..<20 {
            RunLoop.current.run(until: Date().addingTimeInterval(4))
            remote.press(flip % 4 == 3 ? .down : .up)
        }
        RunLoop.current.run(until: Date().addingTimeInterval(4))
        XCTAssertEqual(app.state, .runningForeground)
    }
}
