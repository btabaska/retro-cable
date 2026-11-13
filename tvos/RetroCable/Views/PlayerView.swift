import AVFoundation
import RetroCableKit
import SwiftUI
import UIKit

/// The AVPlayer's picture: an AVPlayerLayer, aspect-fit on black, so 4:3 is pillarboxed and never
/// cropped or stretched (D-5). No AVPlayerViewController, so no transport bar or info panel (TV-6).
struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer

    final class LayerView: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }

    func makeUIView(context _: Context) -> LayerView {
        let view = LayerView()
        view.backgroundColor = UIColor(Palette.black)
        view.playerLayer.videoGravity = .resizeAspect
        view.playerLayer.player = player
        view.isUserInteractionEnabled = false
        return view
    }

    func updateUIView(_ view: LayerView, context _: Context) {
        if view.playerLayer.player !== player { view.playerLayer.player = player }
    }
}

/// Everything in the player's frame: the video, black while tuning ("Tuning…" bars if it takes
/// long), the bumper slate, the filler card and the demo slate. Sized by its frame: full screen or the guide's PiP (design 4.3).
struct PlayerArea: View {
    let tuner: Tuner
    let player: AVPlayer

    var body: some View {
        GeometryReader { geo in
            let h = geo.size.height
            ZStack {
                Palette.black
                PlayerLayerView(player: player)
                switch tuner.picture {
                case .black:
                    Palette.black
                case .video:
                    EmptyView()
                case .bumperSlate(let slate):
                    BumperSlateView(slate: slate, height: h)
                case .filler(let title):
                    FillerCard(title: title, height: h)
                case .tuning:
                    // A tune with no picture after 3 s, or /tune failing: never a black screen that
                    // looks hung (TV-12).
                    FillerCard(title: LiveText.tuning, height: h)
                }
                if let slate = tuner.demoSlate, tuner.picture == .video || tuner.picture == .black {
                    DemoSlateView(slate: slate, size: geo.size)
                }
            }
            .frame(width: geo.size.width, height: h)
            .clipped()
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("player")
    }
}

/// A bumper without media: "02 KTVU", the channel name, "Up next 8:27 Seinfeld".
struct BumperSlateView: View {
    let slate: BumperSlate
    let height: CGFloat

    var body: some View {
        let scale = height / DesignTokens.screen.height
        ZStack {
            Palette.background
            VStack(spacing: height * 0.02) {
                Text(slate.callSign)
                    .font(RCFont.grid(DesignTokens.fontSize.title * scale, bold: true))
                    .foregroundStyle(Palette.text)
                Text(slate.name)
                    .font(RCFont.body(DesignTokens.fontSize.label * scale))
                    .foregroundStyle(Palette.text)
                if let upNext = slate.upNext {
                    Text(upNext)
                        .font(RCFont.body(DesignTokens.fontSize.label * scale))
                        .foregroundStyle(Palette.textMuted)
                }
            }
            .multilineTextAlignment(.center)
            .monospacedDigit()
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("bumper-slate")
    }
}

/// Filler, or a file that failed twice: SMPTE color bars in a 4:3 box with the title.
struct FillerCard: View {
    let title: String
    let height: CGFloat

    /// Fixed broadcast colors, deliberately not tokens (D-2's one exception).
    static let bars: [Color] = [0xC0C0C0, 0xC0C000, 0x00C0C0, 0x00C000, 0xC000C0, 0xC00000, 0x0000C0]
        .map { Color(rc: $0) }

    var body: some View {
        let width = height * 4 / 3
        ZStack(alignment: .bottom) {
            Palette.filler
            VStack(spacing: 0) {
                HStack(spacing: 0) {
                    ForEach(Self.bars.indices, id: \.self) { Self.bars[$0] }
                }
                .frame(height: height * 0.63)
                Spacer(minLength: 0)
            }
            Text(title)
                .font(RCFont.grid(height * 0.07, bold: true))
                .tracking(2)
                .foregroundStyle(Palette.text)
                .lineLimit(1)
                .minimumScaleFactor(0.5)
                .padding(.horizontal, height * 0.04)
                .padding(.bottom, height * 0.12)
        }
        .frame(width: width, height: height)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("player-filler")
    }
}

/// The browser's slate over demo feeds (the test stack only).
struct DemoSlateView: View {
    let slate: DemoSlate
    let size: CGSize

    var body: some View {
        let height = size.height
        VStack(spacing: height * 0.006) {
            Text(slate.title)
                .font(RCFont.grid(height * 0.07, bold: true))
                .foregroundStyle(Palette.text)
            if !slate.detail.isEmpty {
                Text(slate.detail)
                    .font(RCFont.body(height * 0.034))
                    .foregroundStyle(Palette.textMuted)
            }
        }
        .multilineTextAlignment(.center)
        .padding(.vertical, height * 0.02)
        .padding(.horizontal, height * 0.04)
        .frame(maxWidth: height * 0.64)
        .background(Palette.black.opacity(0.72))
        .position(x: size.width / 2, y: height * 0.28)
        .accessibilityIdentifier("player-slate")
    }
}
