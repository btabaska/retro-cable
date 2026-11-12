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
    /// The TV-on opening has finished since the last power on or no signal.
    @State private var tvOpened = false

    var body: some View {
        ZStack(alignment: .topLeading) {
            Palette.header
            switch model.screen {
            case .powerOn:
                PowerOnView(model: model)
            case .noSignal:
                NoSignalView()
            case .live, .guide, .yearPicker:
                TVOn(onOpened: {
                    tvOpened = true
                    model.tvOnOpened()
                }) {
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
                            // The banner waits for the TV-on opening to finish (design 9).
                            LiveTVView(model: model, bannerAllowed: tvOpened)
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
        .onChange(of: model.screen) { _, screen in
            if screen == .powerOn || screen == .noSignal { tvOpened = false }
        }
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

/// Turning the TV on (design 9): a 4 pt white line at mid-screen opens to the full picture over
/// 0.35 s ease-out, the white fading as it opens; then `onOpened` (the banner may show). With
/// Reduce Motion it is a 0.2 s fade.
private struct TVOn<Content: View>: View {
    let onOpened: () -> Void
    @ViewBuilder let content: Content
    @State private var open = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let band = open || reduceMotion ? TVLayout.screen.height : TVLayout.Motion.tvOnLine
        content
            .mask {
                Rectangle()
                    .frame(height: band)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .overlay {
                if !reduceMotion {
                    // The line itself: color.text over the opening band, gone by the time it is open.
                    Rectangle()
                        .fill(Palette.text)
                        .frame(height: band)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .opacity(open ? 0 : 1)
                        .allowsHitTesting(false)
                }
            }
            .opacity(reduceMotion && !open ? 0 : 1)
            .onAppear {
                let animation: Animation = reduceMotion
                    ? .easeInOut(duration: TVLayout.Motion.tvOnReduced)
                    : .easeOut(duration: TVLayout.Motion.tvOn)
                withAnimation(animation, completionCriteria: .logicallyComplete) {
                    open = true
                } completion: {
                    onOpened()
                }
            }
    }
}
