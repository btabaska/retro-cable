import RetroCableKit
import SwiftUI

/// Live TV's overlay above the full-screen player: the clock box and the info banner while the
/// banner shows, and the Siri Remote mapping (TV-13 to TV-17). It takes no visible focus.
struct LiveTVView: View {
    let model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            Color.clear
            if model.bannerVisible {
                TimelineView(.periodic(from: .now, by: 1)) { _ in
                    ZStack {
                        ClockBox(text: model.clockText())
                            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                            .padding(.top, TVLayout.Banner.clockTop)
                            .padding(.trailing, TVLayout.Banner.rightInset)
                        if let content = model.bannerContent() {
                            InfoBannerView(content: content)
                                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                                .padding(.bottom, TVLayout.Banner.bottomInset)
                        }
                    }
                }
                .transition(bannerTransition)
            }
        }
        .frame(width: TVLayout.screen.width, height: TVLayout.screen.height)
        .remoteControl(
            onMove: { model.move($0) },
            onSelect: { model.pressSelect() },
            onPlayPause: { model.pressPlayPause() },
            onBack: model.backLeavesApp ? nil : { model.pressBack() }
        )
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("live")
    }

    private var bannerTransition: AnyTransition {
        let insertion: AnyTransition = reduceMotion ? .opacity : .opacity.combined(with: .offset(y: TVLayout.Banner.slide))
        return .asymmetric(
            insertion: insertion.animation(.easeOut(duration: TVLayout.Banner.fadeIn)),
            removal: .opacity.animation(.easeIn(duration: TVLayout.Banner.fadeOut))
        )
    }
}

extension View {
    /// The cable box remote: one focusable view that reads moves, Select (a click, never a hold),
    /// Play/Pause and Back. `onBack` nil lets Back do the system's thing (leave the app). No focus
    /// look at all (design 8).
    func remoteControl(
        onMove: @escaping (MoveDirection) -> Void,
        onSelect: @escaping () -> Void,
        onPlayPause: @escaping () -> Void,
        onBack: (() -> Void)?
    ) -> some View {
        modifier(RemoteControl(onMove: onMove, onSelect: onSelect, onPlayPause: onPlayPause, onBack: onBack))
    }
}

private struct RemoteControl: ViewModifier {
    let onMove: (MoveDirection) -> Void
    let onSelect: () -> Void
    let onPlayPause: () -> Void
    let onBack: (() -> Void)?
    @FocusState private var focused: Bool

    /// Holding Select this long is a hold (reserved for "like"), not a click (TV-16).
    static let holdSeconds = 0.5

    func body(content: Content) -> some View {
        content
            .contentShape(Rectangle())
            .focusable()
            .focused($focused)
            .focusEffectDisabled()
            .onMoveCommand { direction in
                switch direction {
                case .up: onMove(.up)
                case .down: onMove(.down)
                case .left: onMove(.left)
                case .right: onMove(.right)
                @unknown default: break
                }
            }
            .onPlayPauseCommand(perform: onPlayPause)
            .onExitCommand(perform: onBack)
            .onLongPressGesture(minimumDuration: Self.holdSeconds) {
                // Reserved for "like"; does nothing in this build and never opens the guide.
            }
            .onTapGesture(perform: onSelect)
            .onAppear { focused = true }
    }
}
