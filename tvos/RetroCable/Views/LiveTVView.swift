import RetroCableKit
import SwiftUI

/// Live TV. Milestone 1 holds the screen with black and the channel it will tune; the player,
/// banner and remote mapping arrive in milestone 2 (tvos/HANDOFF.md).
struct LiveTVView: View {
    let model: AppModel

    var body: some View {
        ZStack {
            Palette.black.ignoresSafeArea()
            if let channel = model.channel {
                Text("\(String(format: "%02d", channel.number)) \(channel.callSign)")
                    .font(RCFont.grid(DesignTokens.fontSize.title, bold: true))
                    .foregroundStyle(Palette.textMuted)
            }
        }
        .accessibilityIdentifier("live")
    }
}
