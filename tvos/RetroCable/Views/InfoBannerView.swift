import RetroCableKit
import SwiftUI

/// The info banner (design 4.2): x 96...1824, bottom edge 1016, a 5 pt gold top rule, channel,
/// title row, description, progress and next. A status display: nothing in it takes focus.
struct InfoBannerView: View {
    let content: BannerContent

    private typealias L = TVLayout.Banner

    var body: some View {
        VStack(spacing: 0) {
            Palette.focus.frame(height: L.topRule)
            HStack(alignment: .center, spacing: L.columnGap) {
                channelColumn
                mainColumn
                chips
            }
            .padding(.vertical, L.paddingV)
            .padding(.horizontal, L.paddingH)
            .frame(minHeight: L.minHeight - L.topRule)
            .background(Palette.header)
        }
        .frame(width: L.right - L.left)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("info-banner")
    }

    private var channelColumn: some View {
        VStack(spacing: 0) {
            Text(content.number)
                .font(RCFont.grid(L.numberSize, bold: true))
                .foregroundStyle(Palette.text)
                .monospacedDigit()
                .accessibilityIdentifier("banner-number")
            Text(content.callSign)
                .font(RCFont.body(DesignTokens.fontSize.label, bold: true))
                .foregroundStyle(Palette.textMuted)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(width: L.channelColumnWidth)
    }

    private var mainColumn: some View {
        VStack(alignment: .leading, spacing: L.rowGap) {
            HStack(alignment: .firstTextBaseline, spacing: L.titleGap) {
                Text(content.title)
                    .font(RCFont.body(L.titleSize, bold: true))
                    .foregroundStyle(Palette.text)
                    .lineLimit(1)
                    .layoutPriority(2)
                    .accessibilityIdentifier("banner-title")
                if let episode = content.episode {
                    Text(episode)
                        .font(RCFont.body(DesignTokens.fontSize.body))
                        .foregroundStyle(Palette.textMuted)
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .layoutPriority(0)
                }
                ForEach(content.badges.indices, id: \.self) { index in
                    BadgeView(badge: content.badges[index])
                        .fixedSize()
                        .layoutPriority(1)
                }
            }
            if let description = content.description {
                Text(description)
                    .font(RCFont.body(DesignTokens.fontSize.small))
                    .foregroundStyle(Palette.textMuted)
                    .lineLimit(2)
                    .lineSpacing(L.descriptionLineSpacing)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("banner-description")
            }
            HStack(spacing: L.progressGap) {
                Text(content.start ?? "")
                ProgressTrack(progress: content.progress)
                Text(content.end ?? "")
            }
            .font(RCFont.body(DesignTokens.fontSize.small))
            .foregroundStyle(Palette.textMuted)
            .monospacedDigit()
            HStack(alignment: .firstTextBaseline) {
                if let next = content.next {
                    Text("\(Text(next.time).font(RCFont.body(DesignTokens.fontSize.small, bold: true)).foregroundStyle(Palette.text)) \(next.title)")
                        .font(RCFont.body(DesignTokens.fontSize.small))
                        .foregroundStyle(Palette.textMuted)
                        .lineLimit(1)
                }
                Spacer(minLength: L.progressGap)
                RemoteHints(hints: BannerContent.hints)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var chips: some View {
        VStack(spacing: L.rowGap) {
            Chip(text: "Guide", fill: Palette.rule, color: Palette.text)
            Chip(text: content.year, fill: Palette.focus, color: Palette.onFocus)
        }
        .fixedSize()
    }
}

/// The banner's progress track: `track` with a `focus` fill, stepping with the clock tick.
struct ProgressTrack: View {
    let progress: Double

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.track)
                Capsule().fill(Palette.focus).frame(width: geo.size.width * progress)
            }
        }
        .frame(height: TVLayout.Banner.progressHeight)
    }
}

/// "Guide" and "Year 1998": labels, not buttons.
struct Chip: View {
    let text: String
    let fill: Color
    let color: Color

    var body: some View {
        Text(text)
            .font(RCFont.body(DesignTokens.fontSize.small, bold: true))
            .foregroundStyle(color)
            .padding(.vertical, TVLayout.Banner.chipPaddingV)
            .padding(.horizontal, TVLayout.Banner.chipPaddingH)
            .frame(maxWidth: .infinity)
            .background(fill, in: RoundedRectangle(cornerRadius: DesignTokens.radius.button))
    }
}

/// "▲▼ Channel  SELECT Guide  ▶❚❚ Last channel": key words bold (D-12).
struct RemoteHints: View {
    let hints: [RemoteHint]
    /// The banner's hints are captions in textDim; the guide's footer uses 22 pt text, 36 apart.
    var size: CGFloat = DesignTokens.fontSize.caption
    var spacing: CGFloat = TVLayout.Banner.hintGap
    var color: Color = Palette.textDim

    var body: some View {
        HStack(spacing: spacing) {
            ForEach(hints.indices, id: \.self) { index in
                let hint = hints[index]
                // U+FE0E keeps ▶ a text glyph; without it tvOS draws the emoji.
                let key = hint.key.replacingOccurrences(of: "▶", with: "▶\u{FE0E}")
                Text("\(Text(key).font(RCFont.body(size, bold: true))) \(hint.action)")
                    .font(RCFont.body(size))
                    .foregroundStyle(color)
                    .lineLimit(1)
                    .fixedSize()
            }
        }
    }
}

/// Badges (design 5.5): NEW gold, holiday outlined in holidayMuted, PRETEND PREMIERE outlined in
/// transplant with the drawn diamond first, "S5 · E12" and MOVIE outlined in textMuted.
struct BadgeView: View {
    let badge: BannerContent.Badge

    var body: some View {
        HStack(spacing: TVLayout.Badge.markGap) {
            if badge == .pretendPremiere { TransplantMark(color: Palette.transplant) }
            Text(badge.text)
                .font(RCFont.body(DesignTokens.fontSize.small, bold: true))
                .foregroundStyle(textColor)
        }
        .padding(.vertical, TVLayout.Badge.paddingV)
        .padding(.horizontal, TVLayout.Badge.paddingH)
        .background {
            let shape = RoundedRectangle(cornerRadius: DesignTokens.radius.badge)
            switch badge {
            case .new: shape.fill(Palette.focus)
            case .holiday: shape.strokeBorder(Palette.holidayMuted, lineWidth: TVLayout.Badge.border)
            case .pretendPremiere: shape.strokeBorder(Palette.transplant, lineWidth: TVLayout.Badge.border)
            case .outline: shape.strokeBorder(Palette.textMuted, lineWidth: TVLayout.Badge.border)
            }
        }
    }

    private var textColor: Color {
        switch badge {
        case .new: Palette.onFocus
        case .holiday: Palette.holidayMuted
        case .pretendPremiere, .outline: Palette.text
        }
    }
}

/// The hollow diamond for pretend premieres: a 9 pt square, 2 pt stroke, turned 45°. Drawn, never
/// the "◇" glyph. Rotation doesn't change a view's layout box, so the mark is framed at its turned
/// size (12.7 pt) and the gaps around it are the visible gaps.
struct TransplantMark: View {
    let color: Color

    var body: some View {
        Rectangle()
            .strokeBorder(color, lineWidth: TVLayout.Badge.markStroke)
            .frame(width: TVLayout.Guide.diamondSize, height: TVLayout.Guide.diamondSize)
            .rotationEffect(.degrees(45))
            .frame(width: TVLayout.Guide.diamondExtent, height: TVLayout.Guide.diamondExtent)
            .accessibilityHidden(true)
    }
}

/// The clock box: "7:34 PM" over "Wednesday, Nov 11, 1998", top right (design 4.2).
struct ClockBox: View {
    let text: ClockText

    var body: some View {
        VStack(alignment: .trailing, spacing: 0) {
            Text(text.time)
                .font(RCFont.body(DesignTokens.fontSize.clock, bold: true))
                .foregroundStyle(Palette.text)
            Text(text.date)
                .font(RCFont.body(DesignTokens.fontSize.small))
                .foregroundStyle(Palette.textMuted)
        }
        .monospacedDigit()
        .padding(.vertical, TVLayout.Banner.clockPaddingV)
        .padding(.horizontal, TVLayout.Banner.clockPaddingH)
        .background(Palette.header, in: RoundedRectangle(cornerRadius: DesignTokens.radius.button))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("clock")
    }
}
