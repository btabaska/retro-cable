// Draws the Apple TV app icon and the plain Top Shelf placeholder into the asset catalog
// (design spec section 10): a two-layer icon, back layer the power-on radial gradient, front layer
// "RC" in Archivo Narrow Bold white over a gold 5 pt rule. Colors come from design/tokens.json and
// the font is the bundled TTF, so the art is all ours (no downloaded images).
//
// Run from the repo root after changing the art or the tokens:
//   DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift tvos/tools/make-app-icon.swift
import CoreGraphics
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers

let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
let brand = root.appendingPathComponent("tvos/RetroCable/Resources/Assets.xcassets/App Icon & Top Shelf Image.brandassets")
let tokens = try JSONSerialization.jsonObject(
    with: Data(contentsOf: root.appendingPathComponent("design/tokens.json"))
) as! [String: Any]
let palette = tokens["color"] as! [String: String]

func color(_ name: String) -> CGColor {
    let hex = Int(palette[name]!.dropFirst(), radix: 16)!
    return CGColor(
        srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
        blue: CGFloat(hex & 0xFF) / 255, alpha: 1
    )
}

func font(_ file: String, size: CGFloat) -> CTFont {
    let url = root.appendingPathComponent("tvos/RetroCable/Resources/Fonts/\(file)") as CFURL
    let descriptor = (CTFontManagerCreateFontDescriptorsFromURL(url) as! [CTFontDescriptor])[0]
    return CTFontCreateWithFontDescriptor(descriptor, size, nil)
}

/// Draws into a width x height canvas whose drawing code works in top-left points of a
/// `basis`-wide design (so one drawing serves every scale), and writes a PNG.
func png(_ path: String, width: Int, height: Int, opaque: Bool, draw: (CGContext, CGFloat) -> Void) throws {
    let space = CGColorSpace(name: CGColorSpace.sRGB)!
    let info = opaque ? CGImageAlphaInfo.noneSkipLast.rawValue : CGImageAlphaInfo.premultipliedLast.rawValue
    let context = CGContext(
        data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0, space: space, bitmapInfo: info
    )!
    // Top-left origin, like the design spec.
    context.translateBy(x: 0, y: CGFloat(height))
    context.scaleBy(x: 1, y: -1)
    draw(context, CGFloat(width))
    let url = brand.appendingPathComponent(path)
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, context.makeImage()!, nil)
    guard CGImageDestinationFinalize(dest) else { fatalError("could not write \(path)") }
}

/// The power-on gradient (design 7): glow at 50% / 40% fading to header at 70% of the distance to
/// the farthest corner.
func gradient(_ context: CGContext, width: CGFloat, height: CGFloat) {
    let center = CGPoint(x: width * 0.5, y: height * 0.4)
    let farthest = hypot(width * 0.5, height * 0.6)
    let colors = [color("glow"), color("header")] as CFArray
    let fill = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: colors, locations: [0, 1])!
    context.setFillColor(color("header"))
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    context.drawRadialGradient(
        fill, startCenter: center, startRadius: 0, endCenter: center, endRadius: farthest * 0.7,
        options: [.drawsAfterEndLocation]
    )
}

/// Text centered on `x`, its baseline at `baseline` (top-left coordinates). Returns its width.
@discardableResult
func text(_ string: String, _ context: CGContext, font: CTFont, color fill: CGColor, x: CGFloat, baseline: CGFloat,
          tracking: CGFloat = 0) -> CGFloat
{
    let attributes: [NSAttributedString.Key: Any] = [
        NSAttributedString.Key(kCTFontAttributeName as String): font,
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): fill,
        NSAttributedString.Key(kCTKernAttributeName as String): tracking,
    ]
    let line = CTLineCreateWithAttributedString(NSAttributedString(string: string, attributes: attributes))
    let width = CGFloat(CTLineGetTypographicBounds(line, nil, nil, nil)) - tracking
    context.saveGState()
    // CoreText draws bottom-up: flip back around the baseline.
    context.translateBy(x: x - width / 2, y: baseline)
    context.scaleBy(x: 1, y: -1)
    context.textPosition = .zero
    CTLineDraw(line, context)
    context.restoreGState()
    return width
}

/// The front layer on a 400 x 240 design: "RC" and the gold rule under it, inside the middle of
/// the icon so the focus parallax never crops it.
func front(_ context: CGContext, width: CGFloat) {
    let s = width / 400
    let glyphs = font("ArchivoNarrow-Bold.ttf", size: 132 * s)
    let w = text("RC", context, font: glyphs, color: color("text"), x: 200 * s, baseline: 152 * s, tracking: 4 * s)
    context.setFillColor(color("focus"))
    context.fill(CGRect(x: 200 * s - w / 2, y: 170 * s, width: w, height: 5 * s))
}

func back(_ context: CGContext, width: CGFloat) {
    gradient(context, width: width, height: width * 240 / 400)
}

/// The Top Shelf placeholder: the header color with the wordmark, nothing else (no Top Shelf
/// extension in this build).
func shelf(_ context: CGContext, width: CGFloat, height: CGFloat) {
    context.setFillColor(color("header"))
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    let s = height / 720
    text("RETRO CABLE", context, font: font("ArchivoNarrow-Bold.ttf", size: 96 * s), color: color("text"),
         x: width / 2, baseline: 380 * s, tracking: 8 * s)
    context.setFillColor(color("focus"))
    context.fill(CGRect(x: width / 2 - 160 * s, y: 410 * s, width: 320 * s, height: 5 * s))
}

for (stack, sizes) in [("App Icon.imagestack", [(400, "1x"), (800, "2x")]), ("App Icon - App Store.imagestack", [(1280, "1x")])] {
    for (width, scale) in sizes {
        let height = width * 240 / 400
        let suffix = scale == "1x" ? "" : "@\(scale)"
        try png("\(stack)/Front.imagestacklayer/Content.imageset/front\(suffix).png",
                width: width, height: height, opaque: false) { front($0, width: $1) }
        try png("\(stack)/Back.imagestacklayer/Content.imageset/back\(suffix).png",
                width: width, height: height, opaque: true) { back($0, width: $1) }
    }
}
for (set, width) in [("Top Shelf Image.imageset", 1920), ("Top Shelf Image Wide.imageset", 2320)] {
    for scale in [1, 2] {
        let suffix = scale == 1 ? "" : "@2x"
        try png("\(set)/shelf\(suffix).png", width: width * scale, height: 720 * scale, opaque: true) { context, w in
            shelf(context, width: w, height: CGFloat(720 * scale))
        }
    }
}
print("wrote \(brand.path)")
