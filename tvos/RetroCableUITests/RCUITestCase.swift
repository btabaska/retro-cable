import XCTest

/// Launch, lookup, wait and screenshot helpers shared by the UI tests. It has no tests itself.
@MainActor
class RCUITestCase: XCTestCase {
    let remote = XCUIRemote.shared

    func launch(year: String = "1998", channel: String = "ktvu") -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments += ["-RCNoWatch", "-rc.year", year, "-rc.channel", channel]
        if let server = ProcessInfo.processInfo.environment["RC_SERVER_URL"] {
            app.launchArguments += ["-RCServerURL", server]
        }
        app.launch()
        XCTAssertTrue(element(app, "power-on").waitForExistence(timeout: 10), app.debugDescription)
        return app
    }

    func element(_ app: XCUIApplication, _ id: String) -> XCUIElement {
        app.descendants(matching: .any)[id].firstMatch
    }

    func waitFor(_ condition: @autoclosure () -> Bool, timeout: TimeInterval = 10) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        return condition()
    }

    func number(_ app: XCUIApplication) -> String {
        let number = element(app, "banner-number")
        return number.exists ? number.label : ""
    }

    func snap(_ name: String) {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        if let dir = ProcessInfo.processInfo.environment["RC_SCREENS"], !dir.isEmpty {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try? shot.pngRepresentation.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name).png"))
        }
    }

    /// Live TV on the stored channel, with the banner up.
    func turnedOn(year: String = "1998", channel: String = "ktvu") -> XCUIApplication {
        let app = launch(year: year, channel: channel)
        remote.press(.select)
        XCTAssertTrue(element(app, "live").waitForExistence(timeout: 15))
        XCTAssertTrue(element(app, "info-banner").waitForExistence(timeout: 5))
        return app
    }
}
