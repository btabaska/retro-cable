import RetroCableKit
import SwiftUI

/// The glow-to-header radial gradient behind power on and no signal (design 7).
struct PowerBackground: View {
    var body: some View {
        RadialGradient(
            colors: [Palette.glow, Palette.header],
            center: UnitPoint(x: 0.5, y: TVLayout.Power.gradientCenterY),
            startRadius: 0,
            endRadius: TVLayout.Power.gradientEndRadius
        )
        .ignoresSafeArea()
    }
}

/// Cold launch: the TV is off until Select or Play/Pause (TV-3, TV-4).
struct PowerOnView: View {
    let model: AppModel
    @FocusState private var buttonFocused: Bool

    var body: some View {
        ZStack {
            PowerBackground()
            VStack(spacing: TVLayout.Power.columnGap) {
                Text(PowerOnText.wordmark)
                    .font(RCFont.grid(TVLayout.Power.wordmarkSize, bold: true))
                    .tracking(TVLayout.Power.wordmarkTracking)
                    .foregroundStyle(Palette.text)
                Text(model.placeLine)
                    .font(RCFont.body(DesignTokens.fontSize.label))
                    .foregroundStyle(Palette.textMuted)
                Button {
                    model.pressPower()
                } label: {
                    Text(PowerOnText.button)
                        .font(RCFont.body(DesignTokens.fontSize.label, bold: true))
                        .foregroundStyle(Palette.onFocus)
                        .padding(.vertical, TVLayout.Power.buttonPaddingV)
                        .padding(.horizontal, TVLayout.Power.buttonPaddingH)
                        .background(Palette.focus, in: RoundedRectangle(cornerRadius: DesignTokens.radius.button))
                }
                .buttonStyle(FlatButtonStyle())
                .focusEffectDisabled()
                .focused($buttonFocused)
                .accessibilityIdentifier("power-button")
                VStack(spacing: TVLayout.Power.versionGap) {
                    Text(model.serverLine.text)
                        .font(RCFont.body(DesignTokens.fontSize.small))
                        .foregroundStyle(Palette.textDim)
                        .accessibilityIdentifier("server-version")
                    Text(AppInfo.versionLine())
                        .font(RCFont.body(DesignTokens.fontSize.caption))
                        .foregroundStyle(Palette.textDim)
                        .accessibilityIdentifier("app-version")
                }
            }
            .monospacedDigit()
        }
        .accessibilityIdentifier("power-on")
        .onPlayPauseCommand { model.pressPower() }
        .onAppear { buttonFocused = true }
        .task { await model.loadVersion() }
    }
}

/// A button that draws only its label: no lift, scale, shadow or focus platter (design 8).
struct FlatButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
    }
}
