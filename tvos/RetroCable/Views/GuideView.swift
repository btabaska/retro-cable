import RetroCableKit
import SwiftUI

/// The guide's frame for milestone 2: the header, an empty details panel and the live PiP (the
/// same playback as Live TV, moved). The grid, focus and details arrive in milestone 3.
/// Select or Back returns to Live TV without a retune.
struct GuideView: View {
    let model: AppModel

    private typealias G = TVLayout.Guide

    var body: some View {
        ZStack(alignment: .topLeading) {
            Palette.background
            VStack(spacing: 0) {
                header
                Palette.rule.frame(height: G.headerRule)
                Spacer(minLength: 0)
                footer
            }
            Text("The program grid arrives in the next build.")
                .font(RCFont.body(DesignTokens.fontSize.body))
                .foregroundStyle(Palette.textMuted)
                .padding(.leading, TVLayout.safeSide)
                .padding(.top, G.detailsTop + G.detailsTopPadding)
            // The PiP's border; PlayerArea draws the video inside it, on top of this view.
            Rectangle()
                .strokeBorder(Palette.rule, lineWidth: G.pipBorder)
                .frame(width: G.pipFrame.width, height: G.pipFrame.height)
                .offset(x: G.pipFrame.minX, y: G.pipFrame.minY)
        }
        .frame(width: TVLayout.screen.width, height: TVLayout.screen.height)
        .remoteControl(
            onMove: { _ in },
            onSelect: { model.pressSelect() },
            onPlayPause: { model.pressPlayPause() },
            onBack: { model.pressBack() }
        )
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("guide")
    }

    private var header: some View {
        HStack(alignment: .center) {
            Text("GUIDE")
                .font(RCFont.grid(DesignTokens.fontSize.heading, bold: true))
                .tracking(G.wordmarkTracking)
                .foregroundStyle(Palette.text)
            Spacer()
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                let clock = model.clockText()
                VStack(alignment: .trailing, spacing: 0) {
                    Text(clock.time).font(RCFont.body(DesignTokens.fontSize.clock, bold: true))
                    Text(clock.date).font(RCFont.body(DesignTokens.fontSize.small)).foregroundStyle(Palette.textMuted)
                }
                .foregroundStyle(Palette.text)
                .monospacedDigit()
            }
            Text(Years.label(model.year))
                .font(RCFont.body(DesignTokens.fontSize.clock, bold: true))
                .foregroundStyle(Palette.onFocus)
                .padding(.vertical, G.yearBadgePaddingV)
                .padding(.horizontal, G.yearBadgePaddingH)
                .background(Palette.focus, in: RoundedRectangle(cornerRadius: DesignTokens.radius.button))
                .padding(.leading, G.headerGap)
                .accessibilityIdentifier("year-badge")
        }
        .padding(.top, TVLayout.safeTop)
        .padding(.horizontal, TVLayout.safeSide)
        .frame(height: G.headerHeight)
        .background(Palette.header)
    }

    private var footer: some View {
        RemoteHints(hints: [RemoteHint(key: "SELECT", action: "Watch"), RemoteHint(key: "BACK", action: "Live TV")])
            .frame(maxWidth: .infinity, minHeight: G.footerContentHeight, alignment: .leading)
            .padding(.horizontal, TVLayout.safeSide)
            .frame(height: TVLayout.screen.height - G.footerTop, alignment: .top)
            .background(Palette.header)
    }
}
