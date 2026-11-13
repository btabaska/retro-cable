import AVFoundation
import os
import RetroCableKit
import SwiftUI

@main
struct RetroCableApp: App {
    @State private var engine: AVPlaybackEngine
    @State private var model: AppModel

    init() {
        let engine = AVPlaybackEngine()
        let api = APIClient(baseURL: ServerConfig.current())
        _engine = State(initialValue: engine)
        _model = State(initialValue: AppModel(
            api: api,
            settings: Settings(),
            engine: engine,
            sendWatch: LaunchOptions.watchReportsOff ? { _ in } : nil,
            log: { Logger.playback.notice("\($0, privacy: .public)") }
        ))
        // Sound plays with the TV; nothing else on the Apple TV mixes in.
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)
    }

    var body: some Scene {
        WindowGroup {
            RootView(model: model, player: engine.player)
        }
    }
}

/// Switches for test runs, so a UI test or a simulator session never adds viewing rows to the
/// station manager's data.
enum LaunchOptions {
    /// `-RCNoWatch` or `RC_NO_WATCH=1`: watch reports are dropped.
    static var watchReportsOff: Bool {
        let process = ProcessInfo.processInfo
        return process.arguments.contains("-RCNoWatch") || process.environment["RC_NO_WATCH"] == "1"
    }
}
