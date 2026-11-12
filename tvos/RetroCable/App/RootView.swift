import AVFoundation
import RetroCableKit
import SwiftUI
import UIKit

/// Switches between the screens. Logic lives in AppModel (RetroCableKit); views only draw it and
/// forward remote presses. The one player stays in the tree while the TV is on and moves between
/// full screen (Live TV) and the guide's PiP, so opening the guide never restarts it (TV-27).
struct RootView: View {
    let model: AppModel
    let player: AVPlayer
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ZStack(alignment: .topLeading) {
            Palette.header
            switch model.screen {
            case .powerOn:
                PowerOnView(model: model)
            case .noSignal:
                NoSignalView()
            case .live, .guide, .yearPicker:
                TVOn {
                    ZStack(alignment: .topLeading) {
                        if model.screen == .guide {
                            GuideView(model: model)
                                .transition(.opacity)
                        }
                        // The year picker keeps the player at the PiP's frame, hidden (and stopped),
                        // so going back to the guide doesn't move it.
                        let frame = model.screen == .live ? TVLayout.screen : TVLayout.Guide.pipVideo
                        PlayerArea(tuner: model.tuner, player: player)
                            .frame(width: frame.width, height: frame.height)
                            .offset(x: frame.minX, y: frame.minY)
                            .opacity(model.screen == .yearPicker ? 0 : 1)
                            .allowsHitTesting(false)
                        if model.screen == .live {
                            LiveTVView(model: model)
                        }
                        if model.screen == .yearPicker, let picker = model.yearPicker {
                            YearPickerView(model: model, picker: picker)
                                .transition(.opacity.animation(.easeInOut(duration: TVLayout.Motion.yearPicker)))
                        }
                    }
                    .animation(.easeInOut(duration: TVLayout.Motion.guide), value: model.screen)
                }
            }
        }
        .frame(width: TVLayout.screen.width, height: TVLayout.screen.height)
        .ignoresSafeArea()
        .preferredColorScheme(.dark)
        .tint(Palette.focus)
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background: model.enterBackground()
            case .active: model.enterForeground()
            default: break
            }
        }
        .onChange(of: model.isPlaying, initial: true) { _, playing in
            // TV-39: no screensaver while Live TV or the guide's PiP plays.
            UIApplication.shared.isIdleTimerDisabled = playing
        }
    }
}

/// Turning the TV on: the picture opens from a thin line at mid-screen to full height (design 9).
private struct TVOn<Content: View>: View {
    @ViewBuilder let content: Content
    @State private var open = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        content
            .mask {
                Rectangle()
                    .frame(height: open || reduceMotion ? TVLayout.screen.height : TVLayout.Motion.tvOnLine)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .opacity(reduceMotion && !open ? 0 : 1)
            .onAppear {
                withAnimation(.easeOut(duration: TVLayout.Motion.tvOn)) { open = true }
            }
    }
}
