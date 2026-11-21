import XCTest

/// TV-5, D-11: with no server answering, power on reads "No signal" and Select shows the no-signal
/// card, with nothing focusable on it. The app points at a closed local port, so this runs
/// without the homelab or the test stack.
@MainActor
final class NoSignalTests: RCUITestCase {
    func testNoServerShowsTheNoSignalCard() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments += ["-RCNoWatch", "-rc.year", "1998", "-rc.channel", "ktvu",
                                "-RCServerURL", "https://127.0.0.1:9"]
        app.launch()
        XCTAssertTrue(element(app, "power-on").waitForExistence(timeout: 10), app.debugDescription)
        let server = element(app, "server-version")
        XCTAssertTrue(waitFor(server.exists && server.label == "No signal", timeout: 15),
                      server.exists ? server.label : app.debugDescription)
        remote.press(.select)
        let card = element(app, "no-signal")
        XCTAssertTrue(card.waitForExistence(timeout: 15), app.debugDescription)
        XCTAssertTrue(card.label.contains("Can't reach the cable box server. Retrying…"), card.label)
        XCTAssertFalse(element(app, "live").exists)
        // Presses do nothing while the server is away; it keeps retrying on its own.
        remote.press(.select)
        remote.press(.up)
        RunLoop.current.run(until: Date().addingTimeInterval(1))
        XCTAssertTrue(card.exists)
        snap("tv-no-signal")
    }
}
