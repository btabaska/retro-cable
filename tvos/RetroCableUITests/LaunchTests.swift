import XCTest

/// The start of the browser's flow (TV-3, TV-4, TV-42). Runs once the tvOS Simulator exists;
/// set RC_SERVER_URL in the scheme's test environment to point at the Docker test stack.
@MainActor
final class LaunchTests: XCTestCase {
    func launch() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments += ["-RCNoWatch"]
        if let server = ProcessInfo.processInfo.environment["RC_SERVER_URL"] {
            app.launchArguments += ["-RCServerURL", server]
        }
        app.launch()
        return app
    }

    func testColdLaunchShowsPowerOn() {
        let app = launch()
        XCTAssertTrue(any(app, "power-on").waitForExistence(timeout: 10), app.debugDescription)
        XCTAssertTrue(labelled(app, "RETRO CABLE").exists, app.debugDescription)
        XCTAssertTrue(labelled(app, "Press SELECT to turn on the TV").exists, app.debugDescription)
        let server = any(app, "server-version")
        XCTAssertTrue(server.waitForExistence(timeout: 10))
        let answered = NSPredicate(format: "label BEGINSWITH 'Server v' OR label == 'No signal'")
        expectation(for: answered, evaluatedWith: server)
        waitForExpectations(timeout: 15)
    }

    func testSelectTurnsTheTVOn() {
        let app = launch()
        XCTAssertTrue(any(app, "power-on").waitForExistence(timeout: 10), app.debugDescription)
        XCUIRemote.shared.press(.select)
        let live = any(app, "live")
        let noSignal = any(app, "no-signal")
        let deadline = Date().addingTimeInterval(15)
        while Date() < deadline, !live.exists, !noSignal.exists {
            RunLoop.current.run(until: Date().addingTimeInterval(0.25))
        }
        XCTAssertTrue(live.exists || noSignal.exists)
    }

    func any(_ app: XCUIApplication, _ id: String) -> XCUIElement {
        app.descendants(matching: .any)[id].firstMatch
    }

    func labelled(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", label)).firstMatch
    }
}
