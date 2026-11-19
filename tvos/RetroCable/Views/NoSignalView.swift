import RetroCableKit
import SwiftUI

/// The lineup failed with none in hand: the era card, nothing focusable. AppModel retries every
/// 5 s and switches to Live TV by itself (TV-5).
struct NoSignalView: View {
    var body: some View {
        ZStack {
            PowerBackground()
            VStack(spacing: TVLayout.Power.columnGap) {
                Text(NoSignalText.card)
                    .font(RCFont.grid(TVLayout.Power.noSignalSize, bold: true))
                    .tracking(TVLayout.Power.noSignalTracking)
                    .foregroundStyle(Palette.text)
                    .padding(.vertical, TVLayout.Power.cardPaddingV)
                    .padding(.horizontal, TVLayout.Power.cardPaddingH)
                    .border(Palette.text, width: TVLayout.Power.noSignalBorder)
                Text(NoSignalText.message)
                    .font(RCFont.body(DesignTokens.fontSize.label))
                    .foregroundStyle(Palette.textMuted)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("no-signal")
    }
}
