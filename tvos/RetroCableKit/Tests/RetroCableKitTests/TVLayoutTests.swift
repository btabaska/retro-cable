import CoreGraphics
import Testing
@testable import RetroCableKit

/// The arithmetic in docs/design/tvos.md sections 1, 4, 5 and 6.
@Suite struct TVLayoutTests {
    typealias L = TVLayout

    @Test func safeAreaIsAppleTitleSafe() {
        #expect(L.safe == CGRect(x: 80, y: 60, width: 1760, height: 960))
        #expect(L.screen == CGRect(x: 0, y: 0, width: 1920, height: 1080))
    }

    @Test func guideBandsStackWithoutOverlap() {
        #expect(L.Guide.headerHeight + L.Guide.headerRule == L.Guide.detailsTop)
        #expect(L.Guide.detailsTop + L.Guide.detailsHeight == L.Guide.gridTop)
        #expect(L.Guide.gridTop + L.Guide.timeBarHeight + L.Guide.rowGap == L.Guide.firstRowTop)
        let lastRowBottom = L.Guide.rowTop(L.Guide.visibleRows - 1) + L.Guide.rowHeight
        #expect(lastRowBottom == 964)
        #expect(lastRowBottom <= L.Guide.footerTop)
        #expect(L.Guide.footerTop == 970)
    }

    @Test func cellAreaIs1494PointsForNinetyMinutes() {
        #expect(L.Guide.cellsLeft == 346)
        #expect(L.Guide.cellsWidth == 1494)
        #expect(L.Guide.cellsLeft + L.Guide.cellsWidth == L.safe.maxX)
        #expect(abs(L.Guide.pointsPerMinute - 16.6) < 0.0001)
        #expect(L.Guide.channelColumn == CGRect(x: 80, y: 0, width: 260, height: L.Guide.rowHeight))
    }

    @Test func pipIsAFourByThreeBoxInsideTheSafeArea() {
        #expect(L.Guide.pipVideo == CGRect(x: 1517, y: 146, width: 320, height: 240))
        #expect(L.Guide.pipVideo.width * 3 == L.Guide.pipVideo.height * 4)
        #expect(L.Guide.pipFrame == L.Guide.pipVideo.insetBy(dx: -L.Guide.pipBorder, dy: -L.Guide.pipBorder))
        #expect(L.safe.contains(L.Guide.pipFrame))
        #expect(L.Guide.pipFrame.maxY <= L.Guide.gridTop)
        #expect(L.Guide.detailsTextRight + 40 == L.Guide.pipFrame.minX)
    }

    @Test func snapsToTheRetinaPixel() {
        #expect(L.snap(10.26) == 10.5)
        #expect(L.snap(10.24) == 10)
        #expect(L.cellFrame(left: 1.0 / 3.0, width: 1.0 / 3.0) == CGRect(x: 844, y: 0, width: 498, height: L.Guide.rowHeight))
    }

    @Test func bannerSitsInsideTheSafeArea() {
        #expect(L.Banner.left == 96)
        #expect(L.Banner.right == 1824)
        #expect(L.Banner.bottom == 1016)
        #expect(L.Banner.bottom <= L.safe.maxY)
        #expect(L.Banner.left >= L.safe.minX && L.Banner.right <= L.safe.maxX)
    }

    @Test func yearPickerFitsTwentyFourChannels() {
        let Y = L.YearPicker.self
        #expect(Y.leftWidth == 904)
        #expect(Y.dialLeft - (Y.left + Y.leftWidth) == 56)
        #expect(Y.dialLeft + Y.dialWidth == L.safe.maxX)
        #expect(abs(Y.yearCellWidth - (904 - 96) / 7) < 0.0001)
        #expect(Y.gridHeight == 432)
        #expect(2 * Y.dialColumnWidth + Y.dialColumnGap == Y.dialWidth - 2 * Y.dialPadding)
        // 12 rows of 56 under the title fit the dial panel.
        #expect(Y.dialTitleHeight + 12 + 12 * Y.dialRowHeight <= Y.dialBottom - Y.top - 2 * Y.dialPadding)
    }

    /// Design 4.2: the banner and clock box sit 96 in from the right, the banner's bottom at 1016.
    @Test func bannerInsets() {
        #expect(TVLayout.Banner.rightInset == 96)
        #expect(TVLayout.Banner.left == TVLayout.Banner.rightInset)
        #expect(TVLayout.Banner.bottomInset == 64)
        #expect(TVLayout.Banner.bottom <= TVLayout.safe.maxY)
        #expect(TVLayout.Banner.clockTop == TVLayout.safeTop)
        #expect(TVLayout.Banner.holdSeconds == 4)
        #expect(TVLayout.Banner.fadeIn == 0.2)
        #expect(TVLayout.Banner.fadeOut == 0.3)
    }

    /// Design 5.3: the half-hour labels at x 346, 844 and 1342, 498 apart; the details text stops
    /// 40 pt clear of the PiP.
    @Test func timeBarAndDetailsColumn() {
        #expect((0..<3).map(L.Guide.halfHourLeft) == [346, 844, 1342])
        #expect(L.Guide.detailsTextWidth == 1394)
        #expect(L.Guide.pipFrame.minX - L.Guide.detailsTextRight == 40)
        #expect(L.Guide.rowTop(L.Guide.visibleRows - 1) + L.Guide.rowHeight <= L.Guide.footerTop)
    }

    /// Design 5.3: the 9 pt square turned 45° is 12.7 pt across, so its frame is that wide and the
    /// visible gaps come out at 3 and 10 pt.
    @Test func transplantMarkTakesItsRotatedWidth() {
        #expect(abs(L.Guide.diamondExtent - 12.728) < 0.001)
        #expect(abs(L.Guide.diamondRoom - (3 + 12.728 + 10)) < 0.001)
    }

    /// Design 9: TV on opens over 0.35 s; with Reduce Motion it is a 0.2 s fade.
    @Test func tvOnTimings() {
        #expect(L.Motion.tvOn == 0.35)
        #expect(L.Motion.tvOnReduced == 0.2)
        #expect(L.Motion.tvOnLine == 4)
    }

    /// Design 6: the year grid starts about y 399 and ends about y 831, inside the safe area; the
    /// dial's list is 734 pt tall.
    @Test func yearPickerColumns() {
        let Y = L.YearPicker.self
        #expect(Y.dialListHeight == 734)
        #expect(Y.dialHeight == 840)
        #expect(Y.top + Y.dialHeight == L.safe.maxY)
    }
}
