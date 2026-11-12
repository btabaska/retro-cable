import CoreGraphics

/// tvOS-only geometry from docs/design/tvos.md, in points on the 1920 x 1080 canvas. Not shared
/// with the web, so not tokens; TVLayoutTests checks the arithmetic.
public enum TVLayout {
    public static let screen = CGRect(x: 0, y: 0, width: DesignTokens.screen.width, height: DesignTokens.screen.height)
    /// Apple's title-safe margins for TVs that overscan.
    public static let safeTop: CGFloat = 60
    public static let safeBottom: CGFloat = 60
    /// The web's 64 pt gutter becomes 80 on tvOS.
    public static let safeSide: CGFloat = 80
    public static let safe = CGRect(
        x: safeSide, y: safeTop, width: screen.width - 2 * safeSide, height: screen.height - safeTop - safeBottom
    )

    /// Rounds to the @2x pixel (0.5 pt) so edges don't shimmer.
    public static func snap(_ value: CGFloat) -> CGFloat {
        (value * 2).rounded() / 2
    }

    /// A guide cell's frame in row coordinates from its window fractions.
    public static func cellFrame(left: Double, width: Double) -> CGRect {
        CGRect(
            x: snap(Guide.cellsLeft + CGFloat(left) * Guide.cellsWidth),
            y: 0,
            width: snap(CGFloat(width) * Guide.cellsWidth),
            height: Guide.rowHeight
        )
    }

    /// Section 5: the guide.
    public enum Guide {
        public static let headerHeight: CGFloat = 129
        public static let headerRule: CGFloat = 3
        public static let detailsTop: CGFloat = 132
        public static let detailsHeight: CGFloat = 266
        public static let detailsTopPadding: CGFloat = 14
        public static let detailsLineGap: CGFloat = 6
        public static let detailsTextRight: CGFloat = 1474
        public static let gridTop: CGFloat = 398
        public static let timeBarHeight: CGFloat = 48
        public static let rowHeight: CGFloat = 68
        public static let rowGap: CGFloat = DesignTokens.space.rowGap
        public static let firstRowTop: CGFloat = gridTop + timeBarHeight + rowGap
        public static let visibleRows = 7
        /// The focused row sits 4th from the top whenever the list allows.
        public static let focusedRowSlot = 3
        public static let footerTop: CGFloat = 970
        public static let channelColumnWidth: CGFloat = 260
        public static let channelColumn = CGRect(x: TVLayout.safeSide, y: 0, width: channelColumnWidth, height: rowHeight)
        public static let tunedBarWidth: CGFloat = 6
        public static let cellsLeft: CGFloat = TVLayout.safeSide + channelColumnWidth + DesignTokens.space.rowGap
        public static let cellsWidth: CGFloat = TVLayout.safe.maxX - cellsLeft
        public static let windowMinutes: CGFloat = 90
        public static let pointsPerMinute: CGFloat = cellsWidth / windowMinutes
        public static let halfHourWidth: CGFloat = cellsWidth / 3
        public static let pipBorder: CGFloat = 3
        public static let pipVideo = CGRect(x: 1517, y: 146, width: 320, height: 240)
        public static let pipFrame = pipVideo.insetBy(dx: -pipBorder, dy: -pipBorder)
        /// Header: wordmark tracking, the 40 pt gaps, the year badge's padding.
        public static let wordmarkTracking: CGFloat = 3
        public static let headerGap: CGFloat = 40
        public static let yearBadgePaddingV: CGFloat = 8
        public static let yearBadgePaddingH: CGFloat = 24
        /// Footer content sits in y 970...1020.
        public static let footerContentHeight: CGFloat = 50
        public static let holidayBarWidth: CGFloat = 3
        public static let diamondSize: CGFloat = 9

        public static func rowTop(_ index: Int) -> CGFloat {
            firstRowTop + CGFloat(index) * (rowHeight + rowGap)
        }
    }

    /// Section 4.2: the info banner and clock box.
    public enum Banner {
        public static let left: CGFloat = 96
        public static let right: CGFloat = 1824
        public static let bottom: CGFloat = 1016
        public static let minHeight: CGFloat = 190
        public static let topRule: CGFloat = 5
        public static let paddingV: CGFloat = 20
        public static let paddingH: CGFloat = 40
        public static let channelColumnWidth: CGFloat = 160
        public static let columnGap: CGFloat = 48
        public static let rowGap: CGFloat = 12
        public static let clockTop: CGFloat = 60
        public static let numberSize: CGFloat = 88
        public static let titleSize: CGFloat = 50
        public static let progressHeight: CGFloat = 10
        /// Between the title and the episode label and badges.
        public static let titleGap: CGFloat = 20
        /// 2-line description at line height 1.3 of 22 pt.
        public static let descriptionLineSpacing: CGFloat = 6.6
        /// Between the progress times and the track; also the least gap before the hints.
        public static let progressGap: CGFloat = 16
        public static let hintGap: CGFloat = 16
        public static let chipPaddingV: CGFloat = 12
        public static let chipPaddingH: CGFloat = 28
        public static let clockPaddingV: CGFloat = 12
        public static let clockPaddingH: CGFloat = 24
        /// The banner and the clock box sit this far in from the right edge (1920 - 1824).
        public static let rightInset: CGFloat = TVLayout.screen.width - right
        /// The banner's bottom edge is 1016: 64 above the screen's bottom.
        public static let bottomInset: CGFloat = TVLayout.screen.height - bottom
        /// Real seconds the banner holds after its latest trigger.
        public static let holdSeconds: Double = 4
        /// Fade in (with a 24 pt slide up) and fade out.
        public static let fadeIn: Double = 0.2
        public static let fadeOut: Double = 0.3
        public static let slide: CGFloat = 24
    }

    /// Section 5.5: badges in the banner and the details panel.
    public enum Badge {
        public static let paddingV: CGFloat = 2
        public static let paddingH: CGFloat = 10
        public static let border: CGFloat = 2
        public static let markStroke: CGFloat = 2
        public static let markGap: CGFloat = 8
    }

    /// Section 9: motion.
    public enum Motion {
        /// Live <-> guide: the guide fades and the player moves between full screen and the PiP.
        public static let guide: Double = 0.2
        /// Power on -> Live TV: the picture opens from a 4 pt line.
        public static let tvOn: Double = 0.35
        public static let tvOnLine: CGFloat = 4
    }

    /// Section 6: the year picker.
    public enum YearPicker {
        public static let top: CGFloat = 180
        public static let left: CGFloat = TVLayout.safeSide
        public static let leftWidth: CGFloat = 904
        public static let dialLeft: CGFloat = 1040
        public static let dialWidth: CGFloat = 800
        public static let dialBottom: CGFloat = TVLayout.safe.maxY
        public static let dialPadding: CGFloat = 32
        public static let dialTitleHeight: CGFloat = 30
        public static let dialColumnGap: CGFloat = 24
        public static let dialColumnWidth: CGFloat = 356
        public static let dialRowHeight: CGFloat = 56
        public static let dialRowMinHeight: CGFloat = 44
        public static let columns = 7
        public static let yearCellGap: CGFloat = 16
        public static let yearCellHeight: CGFloat = 96
        public static let yearCellWidth: CGFloat = (leftWidth - CGFloat(columns - 1) * yearCellGap) / CGFloat(columns)
        public static let gridHeight: CGFloat = 4 * yearCellHeight + 3 * yearCellGap
    }

    /// Section 7: power on and no signal.
    public enum Power {
        public static let wordmarkSize: CGFloat = 140
        public static let wordmarkTracking: CGFloat = 12
        public static let noSignalSize: CGFloat = 120
        public static let noSignalTracking: CGFloat = 10
        public static let noSignalBorder: CGFloat = 6
        public static let columnGap: CGFloat = 32
        public static let gradientCenterY: CGFloat = 0.4
        /// 70% of the 1158 pt farthest-corner distance: the web's `circle at 50% 40%`.
        public static let gradientEndRadius: CGFloat = 811
        public static let buttonPaddingV: CGFloat = 20
        public static let buttonPaddingH: CGFloat = 48
        public static let versionGap: CGFloat = 8
        public static let cardPaddingV: CGFloat = 24
        public static let cardPaddingH: CGFloat = 64
    }
}
