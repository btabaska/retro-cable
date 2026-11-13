import RetroCableKit
import SwiftUI

/// The guide (design 5): header, details panel with the live PiP, 7 rows x 90 minutes, footer.
/// One focusable view reads the remote; the focused cell is model state (`GuideGrid`), drawn as a
/// solid gold fill with no system focus look (design 8). The PiP's video is the app's one player,
/// which RootView draws on top of this view inside the PiP border.
struct GuideView: View {
    let model: AppModel

    private typealias G = TVLayout.Guide

    var body: some View {
        // A 1 s tick keeps the clock and the past-cell dimming on simulated time.
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            let now = model.simNow()
            ZStack(alignment: .topLeading) {
                Palette.background
                GuideHeader(clock: model.clockText(), year: Years.label(model.year))
                GuideDetailsPanel(details: model.guideDetails(), status: model.guideStatus)
                    .offset(x: TVLayout.safeSide, y: G.detailsTop + G.detailsTopPadding)
                // The PiP's border; PlayerArea draws the video inside it, on top of this view.
                Rectangle()
                    .strokeBorder(Palette.rule, lineWidth: G.pipBorder)
                    .frame(width: G.pipFrame.width, height: G.pipFrame.height)
                    .offset(x: G.pipFrame.minX, y: G.pipFrame.minY)
                if let grid = model.guide {
                    GuideTimeBarView(bar: grid.timeBar(zone: model.zone))
                        .offset(y: G.gridTop)
                    GuideRows(grid: grid, tuned: model.channelID, now: now, zone: model.zone)
                }
                GuideFooter()
                    .offset(y: G.footerTop)
            }
        }
        .frame(width: TVLayout.screen.width, height: TVLayout.screen.height, alignment: .topLeading)
        .remoteControl(
            onMove: { model.move($0) },
            onSelect: { model.pressSelect() },
            onPlayPause: { model.pressPlayPause() },
            onBack: { model.pressBack() }
        )
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("guide")
    }
}

/// "GUIDE", the "All Channels" tab, the clock and the year badge (design 5.1).
private struct GuideHeader: View {
    let clock: ClockText
    let year: String

    private typealias G = TVLayout.Guide

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: G.headerGap) {
                Text("GUIDE")
                    .font(RCFont.grid(DesignTokens.fontSize.heading, bold: true))
                    .tracking(G.wordmarkTracking)
                    .foregroundStyle(Palette.text)
                Text("All Channels")
                    .font(RCFont.body(DesignTokens.fontSize.small, bold: true))
                    .foregroundStyle(Palette.text)
                    .padding(.vertical, G.tabPaddingV)
                    .padding(.horizontal, G.tabPaddingH)
                    .background(Palette.rule, in: RoundedRectangle(cornerRadius: DesignTokens.radius.button))
                Spacer(minLength: 0)
                VStack(alignment: .trailing, spacing: 0) {
                    Text(clock.time).font(RCFont.body(DesignTokens.fontSize.clock, bold: true))
                    Text(clock.date).font(RCFont.body(DesignTokens.fontSize.small)).foregroundStyle(Palette.textMuted)
                }
                .foregroundStyle(Palette.text)
                .monospacedDigit()
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("clock")
                Text(year)
                    .font(RCFont.body(DesignTokens.fontSize.clock, bold: true))
                    .foregroundStyle(Palette.onFocus)
                    .padding(.vertical, G.yearBadgePaddingV)
                    .padding(.horizontal, G.yearBadgePaddingH)
                    .background(Palette.focus, in: RoundedRectangle(cornerRadius: DesignTokens.radius.button))
                    .accessibilityIdentifier("year-badge")
            }
            .padding(.top, TVLayout.safeTop)
            .padding(.horizontal, TVLayout.safeSide)
            .frame(width: TVLayout.screen.width, height: G.headerHeight)
            .background(Palette.header)
            Palette.rule.frame(width: TVLayout.screen.width, height: G.headerRule)
        }
    }
}

/// The focused cell's details (design 5.2): a fixed-height text column left of the PiP.
private struct GuideDetailsPanel: View {
    let details: GuideDetails?
    let status: AppModel.GuideStatus

    private typealias G = TVLayout.Guide

    var body: some View {
        VStack(alignment: .leading, spacing: G.detailsLineGap) {
            if let details {
                Text(details.line)
                    .font(RCFont.body(DesignTokens.fontSize.body, bold: true))
                    .foregroundStyle(Palette.textMuted)
                    .lineLimit(1)
                Text(details.title)
                    .font(RCFont.body(DesignTokens.fontSize.title, bold: true))
                    .foregroundStyle(Palette.text)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .accessibilityIdentifier("details-title")
                if let episode = details.episode {
                    Text(episode)
                        .font(RCFont.body(DesignTokens.fontSize.label))
                        .foregroundStyle(Palette.text)
                        .lineLimit(1)
                }
                HStack(alignment: .center, spacing: G.badgeGap) {
                    ForEach(details.badges.indices, id: \.self) { index in
                        BadgeView(badge: details.badges[index]).fixedSize()
                    }
                    Text(details.aired)
                        .font(RCFont.body(DesignTokens.fontSize.small))
                        .foregroundStyle(Palette.textMuted)
                        .lineLimit(1)
                }
                if let synopsis = details.synopsis {
                    Text(synopsis)
                        .font(RCFont.body(DesignTokens.fontSize.small, italic: true))
                        .foregroundStyle(Palette.textDim)
                        .lineLimit(2)
                        .lineSpacing(G.synopsisLineSpacing)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("details-synopsis")
                }
            } else {
                Text(status == .failed ? GuideDetails.noSignal : GuideDetails.loading)
                    .font(RCFont.body(DesignTokens.fontSize.title, bold: true))
                    .foregroundStyle(Palette.text)
                    .accessibilityIdentifier("details-title")
            }
        }
        .frame(
            width: G.detailsTextWidth, height: G.detailsHeight - G.detailsTopPadding, alignment: .topLeading
        )
        .clipped()
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("guide-details")
    }
}

/// The day and three half-hour labels (design 5.3).
private struct GuideTimeBarView: View {
    let bar: GuideTimeBar

    private typealias G = TVLayout.Guide

    var body: some View {
        ZStack(alignment: .topLeading) {
            Palette.header
                .frame(width: TVLayout.safe.width, height: G.timeBarHeight)
                .offset(x: TVLayout.safeSide)
            label(bar.day)
                .padding(.leading, G.dayPaddingLeading)
                .frame(width: G.channelColumnWidth, height: G.timeBarHeight, alignment: .leading)
                .offset(x: TVLayout.safeSide)
            ForEach(bar.labels.indices, id: \.self) { index in
                HStack(spacing: 0) {
                    Palette.rule.frame(width: G.timeRule)
                    label(bar.labels[index]).padding(.leading, G.timeInset)
                    Spacer(minLength: 0)
                }
                .frame(width: G.halfHourWidth, height: G.timeBarHeight)
                .offset(x: G.halfHourLeft(index))
            }
        }
    }

    private func label(_ text: String) -> some View {
        Text(text)
            .font(RCFont.grid(DesignTokens.fontSize.body, bold: true))
            .foregroundStyle(Palette.text)
            .monospacedDigit()
            .lineLimit(1)
    }
}

/// The seven visible rows: channel column and cells at true run times.
private struct GuideRows: View {
    let grid: GuideGrid
    let tuned: String?
    let now: Date
    let zone: String

    private typealias G = TVLayout.Guide

    var body: some View {
        ForEach(Array(grid.visibleRows), id: \.self) { rowIndex in
            let row = grid.rows[rowIndex]
            let slot = rowIndex - grid.firstRow
            ZStack(alignment: .topLeading) {
                ChannelCell(channel: row.channel, tuned: row.channel.id == tuned)
                    .offset(x: G.channelColumn.minX)
                ForEach(grid.windowCells(rowIndex), id: \.start) { cell in
                    let frame = TVLayout.cellFrame(left: cell.left, width: cell.width)
                    let content = GuideCellContent.make(
                        cell, focused: grid.isFocused(row: rowIndex, cell), now: now, zone: zone
                    )
                    ProgramCell(content: content)
                        .frame(width: max(0, frame.width - DesignTokens.space.cellGap), height: G.rowHeight)
                        .offset(x: frame.minX)
                }
            }
            .frame(width: TVLayout.screen.width, height: G.rowHeight, alignment: .topLeading)
            .offset(y: G.rowTop(slot))
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("guide-row-\(row.channel.id)")
        }
    }
}

/// "02 KTVU": the channel column, with a 6 pt gold bar on the tuned channel.
private struct ChannelCell: View {
    let channel: ChannelInfo
    let tuned: Bool

    private typealias G = TVLayout.Guide

    var body: some View {
        HStack(spacing: G.channelNumberGap) {
            Text(LiveText.number(channel.number))
                .font(RCFont.grid(DesignTokens.fontSize.label, bold: true))
                .foregroundStyle(Palette.text)
                .monospacedDigit()
            Text(channel.callSign)
                .font(RCFont.grid(DesignTokens.fontSize.body))
                .foregroundStyle(Palette.textMuted)
                .lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, G.channelPaddingH)
        .frame(width: G.channelColumnWidth, height: G.rowHeight)
        // A shape, not a bare color: a color background runs on into the safe area (to x 0).
        .background { Rectangle().fill(Palette.channel) }
        .overlay(alignment: .leading) {
            if tuned { Palette.focus.frame(width: G.tunedBarWidth) }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(tuned ? .isSelected : [])
    }
}

/// One program cell (design 5.3, D-8): the width class decides the text; focus is a gold fill
/// with navy bold text and nothing else.
private struct ProgramCell: View {
    let content: GuideCellContent

    private typealias G = TVLayout.Guide

    var body: some View {
        ZStack(alignment: .leading) {
            fill
            if content.holiday {
                (content.focused ? Palette.onFocus : Palette.holidayMuted).frame(width: G.holidayBarWidth)
            }
            switch content.size {
            case .full:
                VStack(alignment: .leading, spacing: 0) {
                    timeLine
                    titleLine
                }
                .padding(.vertical, G.cellPadV)
                .padding(.horizontal, DesignTokens.space.cellPad)
            case .narrow:
                titleLine
                    .padding(.leading, G.narrowPadLeading)
                    .padding(.trailing, G.narrowPadTrailing)
            case .sliver:
                EmptyView()
            }
        }
        .clipped()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(content.accessibilityLabel)
        .accessibilityAddTraits(content.focused ? .isSelected : [])
    }

    private var fill: Color {
        if content.focused { return Palette.focus }
        return content.past ? Palette.cellPast : Palette.cell
    }

    private var muted: Color { content.focused ? Palette.onFocus : Palette.textMuted }

    private var timeLine: some View {
        var line = Text(content.time ?? "")
        for part in content.kicker {
            let word = part.tone == .new
                ? Text(part.text)
                    .font(RCFont.grid(DesignTokens.fontSize.caption, bold: true))
                    .foregroundStyle(content.focused ? Palette.onFocus : Palette.focus)
                : Text(part.text)
            line = Text("\(line) · \(word)")
        }
        return line
            .font(RCFont.grid(DesignTokens.fontSize.caption))
            .foregroundStyle(muted)
            .lineLimit(1)
    }

    private var titleLine: some View {
        HStack(spacing: 0) {
            if content.transplant {
                TransplantMark(color: content.focused ? Palette.onFocus : Palette.transplant)
                    .padding(.leading, G.diamondBefore)
                    .padding(.trailing, G.diamondAfter)
            }
            // Nil when a letter and the ellipsis don't fit (the kit decides): no lone "…".
            if let title = content.title {
                Text(title)
                    .font(RCFont.grid(DesignTokens.fontSize.body, bold: content.focused))
                    .foregroundStyle(content.focused ? Palette.onFocus : Palette.text)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .allowsTightening(true)
            }
        }
    }
}

/// "SELECT Watch  ▶❚❚ Change year  BACK Live TV", and the pretend-premiere key (design 5.4).
private struct GuideFooter: View {
    private typealias G = TVLayout.Guide

    var body: some View {
        HStack(spacing: 0) {
            RemoteHints(
                hints: GuideDetails.hints, size: DesignTokens.fontSize.small, spacing: G.footerGap, color: Palette.text
            )
            Spacer(minLength: 0)
            HStack(spacing: TVLayout.Badge.markGap) {
                TransplantMark(color: Palette.transplant)
                Text(GuideDetails.pretendPremiereLegend)
                    .font(RCFont.body(DesignTokens.fontSize.small))
                    .foregroundStyle(Palette.textMuted)
            }
        }
        .frame(height: G.footerContentHeight)
        .padding(.horizontal, TVLayout.safeSide)
        .frame(width: TVLayout.screen.width, height: TVLayout.screen.height - G.footerTop, alignment: .top)
        .background(Palette.header)
    }
}
