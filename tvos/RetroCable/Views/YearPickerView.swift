import RetroCableKit
import SwiftUI

/// The year picker (design 6): "SET YOUR YEAR", the focused year's date and copy, the 7 x 3 year
/// grid with All Years below, and the dial in two columns. Focus is model state drawn as a gold
/// fill; the year now playing has a ring. The picture is hidden and silent while it shows.
struct YearPickerView: View {
    let model: AppModel
    let picker: YearPickerModel

    private typealias Y = TVLayout.YearPicker

    var body: some View {
        let panel = picker.panel
        ZStack(alignment: .topLeading) {
            Palette.background
            header
            VStack(alignment: .leading, spacing: 0) {
                Text(panel.kicker)
                    .font(RCFont.body(DesignTokens.fontSize.body))
                    .foregroundStyle(Palette.textMuted)
                Text(panel.date)
                    .font(RCFont.body(DesignTokens.fontSize.title, bold: true))
                    .foregroundStyle(Palette.text)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .padding(.top, Y.dateGap)
                    .accessibilityIdentifier("year-date")
                Text(panel.sub)
                    .font(RCFont.body(DesignTokens.fontSize.body))
                    .foregroundStyle(Palette.textMuted)
                    .lineLimit(2, reservesSpace: true)
                    .padding(.top, Y.subGap)
                YearGridView(focused: picker.focused, current: picker.current)
                    .padding(.top, Y.gridGap)
            }
            .frame(width: Y.leftWidth, alignment: .leading)
            .offset(x: Y.left, y: Y.top)
            DialPanel(panel: panel)
                .offset(x: Y.dialLeft, y: Y.top)
        }
        .frame(width: TVLayout.screen.width, height: TVLayout.screen.height, alignment: .topLeading)
        .remoteControl(
            onMove: { model.move($0) },
            onSelect: { model.pressSelect() },
            onPlayPause: {},
            onBack: { model.pressBack() }
        )
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("year-picker")
    }

    private var header: some View {
        VStack(spacing: 0) {
            HStack {
                Text("SET YOUR YEAR")
                    .font(RCFont.grid(DesignTokens.fontSize.heading, bold: true))
                    .tracking(TVLayout.Guide.wordmarkTracking)
                    .foregroundStyle(Palette.text)
                Spacer(minLength: 0)
                RemoteHints(
                    hints: [RemoteHint(key: "BACK", action: model.yearPickerBack)], size: DesignTokens.fontSize.small,
                    color: Palette.text
                )
            }
            .padding(.top, TVLayout.safeTop)
            .padding(.horizontal, TVLayout.safeSide)
            .frame(width: TVLayout.screen.width, height: TVLayout.Guide.headerHeight)
            .background(Palette.header)
            Palette.rule.frame(width: TVLayout.screen.width, height: TVLayout.Guide.headerRule)
        }
    }
}

/// 1990-2010 in rows of seven and All Years across the fourth row (TV-29, D-10).
private struct YearGridView: View {
    let focused: Int
    let current: Int

    private typealias Y = TVLayout.YearPicker

    var body: some View {
        VStack(alignment: .leading, spacing: Y.yearCellGap) {
            ForEach(YearGrid.rows.indices, id: \.self) { row in
                HStack(spacing: Y.yearCellGap) {
                    ForEach(YearGrid.rows[row], id: \.self) { year in
                        YearCell(year: year, focused: year == focused, current: year == current)
                            .frame(width: year == Years.all ? Y.leftWidth : Y.yearCellWidth, height: Y.yearCellHeight)
                    }
                }
            }
        }
    }
}

private struct YearCell: View {
    let year: Int
    let focused: Bool
    let current: Bool

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: DesignTokens.radius.button)
        Text(Years.label(year))
            .font(RCFont.grid(DesignTokens.fontSize.heading, bold: true))
            .foregroundStyle(focused ? Palette.onFocus : Palette.text)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(focused ? Palette.focus : Palette.cell, in: shape)
            .overlay {
                if current { shape.strokeBorder(Palette.textMuted, lineWidth: TVLayout.YearPicker.ringWidth) }
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("year-\(year)")
            .accessibilityAddTraits(focused ? .isSelected : [])
    }
}

/// "On the dial in 1998": every channel by number in two columns; off-air channels dimmed.
private struct DialPanel: View {
    let panel: YearPanel

    private typealias Y = TVLayout.YearPicker

    var body: some View {
        VStack(alignment: .leading, spacing: Y.dialTitleGap) {
            Text(panel.dialTitle)
                .font(RCFont.body(DesignTokens.fontSize.body, bold: true))
                .foregroundStyle(Palette.text)
                .frame(height: Y.dialTitleHeight, alignment: .leading)
            if let dial = panel.dial {
                let layout = DialColumns.make(dial)
                HStack(alignment: .top, spacing: Y.dialColumnGap) {
                    ForEach(layout.columns.indices, id: \.self) { column in
                        VStack(spacing: 0) {
                            ForEach(layout.columns[column]) { entry in
                                DialRow(entry: entry).frame(height: layout.rowHeight)
                            }
                            if column == layout.columns.count - 1, let more = layout.moreText {
                                Text(more)
                                    .font(RCFont.body(DesignTokens.fontSize.small))
                                    .foregroundStyle(Palette.textMuted)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .frame(height: layout.rowHeight)
                            }
                        }
                        .frame(width: Y.dialColumnWidth)
                    }
                }
            }
        }
        .padding(Y.dialPadding)
        .frame(width: Y.dialWidth, height: Y.dialHeight, alignment: .topLeading)
        .background(Palette.header, in: RoundedRectangle(cornerRadius: Y.dialRadius))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("year-dial")
    }
}

private struct DialRow: View {
    let entry: DialEntry

    private typealias Y = TVLayout.YearPicker

    var body: some View {
        HStack(spacing: Y.dialNumberGap) {
            Text(entry.number)
                .font(RCFont.grid(DesignTokens.fontSize.body, bold: true))
                .foregroundStyle(entry.onAir ? Palette.text : Palette.textDim)
                .monospacedDigit()
                .frame(width: Y.dialNumberWidth, alignment: .leading)
            VStack(alignment: .leading, spacing: 0) {
                Text(entry.name)
                    .font(RCFont.body(DesignTokens.fontSize.small))
                    .foregroundStyle(entry.onAir ? Palette.text : Palette.textDim)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Text(entry.note)
                    .font(RCFont.body(DesignTokens.fontSize.caption))
                    .foregroundStyle(Palette.textMuted)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .frame(maxHeight: .infinity)
        .overlay(alignment: .bottom) {
            Palette.divider.frame(height: Y.dialDivider)
        }
        .accessibilityElement(children: .combine)
    }
}
