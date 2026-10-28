// Deterministic vector/raster brand exports. Does not load the app or Photos.
import AppKit
import CoreGraphics
import Foundation

let destination = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "assets/brand")
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
let navy = NSColor(srgbRed: 7/255, green: 30/255, blue: 42/255, alpha: 1)
let orange = NSColor(srgbRed: 1, green: 91/255, blue: 36/255, alpha: 1)
let cyan = NSColor(srgbRed: 176/255, green: 232/255, blue: 240/255, alpha: 1)
let tagline = "Action, packed lighter"

enum Artwork { case mark, darkMark, lockup, darkLockup, monochrome, vertical, icon, board }

func geometry(monochrome: Bool = false, dark: Bool = false) -> String {
    let ink = dark ? "#FFFFFF" : "#071E2A"
    let top = monochrome ? ink : "#FF5B24"
    let tile = monochrome ? ink : "#B0E8F0"
    let play = monochrome && !dark ? "#FFFFFF" : "#071E2A"
    let strokes = """
    <g id="downward-motion-lines" stroke="\(ink)" stroke-width="10" stroke-linecap="round">
      <path d="M224 48V76"/><path d="M256 20V76"/><path d="M288 48V76"/>
    </g>
    """
    return """
    \(strokes)
    <g fill="\(top)">
      <rect x="221" y="108" width="70" height="88" rx="18"/>
      <rect x="82" y="166" width="348" height="66" rx="22"/>
    </g>
    <rect x="98" y="242" width="316" height="102" rx="51" fill="\(tile)"/>
    <path d="M232 269Q232 259 241 264L283 287Q293 293 283 299L241 323Q232 329 232 319Z" fill="\(play)"/>
    <rect x="68" y="354" width="376" height="50" rx="16" fill="\(ink)"/>
    """
}

func lettering(dark: Bool = false) -> String {
    let ink = dark ? "#FFFFFF" : "#071E2A"
    return """
    <text x="500" y="278" fill="\(ink)" font-family="SF Pro Rounded, Arial Rounded MT Bold, Helvetica, sans-serif" font-size="154" font-weight="900" letter-spacing="-5">CamPresso</text>
    <text x="600" y="350" fill="\(ink)" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="44" letter-spacing="10">\(tagline)</text>
    """
}

let iconGeometry = """
<rect x="12" y="12" width="488" height="488" rx="110" fill="#071E2A"/>
<g transform="translate(15 59) scale(.94)">\(geometry(dark: true))</g>
"""

func svg(_ name: String, width: Int = 512, height: Int = 512, body: String) throws {
    let source = """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 \(width) \(height)" role="img" aria-label="CamPresso — \(tagline)">
    <title>CamPresso</title>
    \(body)
    </svg>
    """
    try source.write(to: destination.appendingPathComponent(name), atomically: true, encoding: .utf8)
}

func rectangle(_ context: CGContext, _ rect: CGRect, radius: CGFloat, color: NSColor) {
    context.setFillColor(color.cgColor)
    context.addPath(CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil))
    context.fillPath()
}

func press(_ context: CGContext, monochrome: Bool = false, dark: Bool = false) {
    let ink: NSColor = dark ? .white : navy
    context.setStrokeColor(ink.cgColor); context.setLineWidth(10); context.setLineCap(.round)
    for (x, y) in [(224.0, 48.0), (256, 20), (288, 48)] {
        context.move(to: .init(x: x, y: y)); context.addLine(to: .init(x: x, y: 76)); context.strokePath()
    }
    let top = monochrome ? ink : orange
    rectangle(context, .init(x: 221, y: 108, width: 70, height: 88), radius: 18, color: top)
    rectangle(context, .init(x: 82, y: 166, width: 348, height: 66), radius: 22, color: top)
    rectangle(context, .init(x: 98, y: 242, width: 316, height: 102), radius: 51, color: monochrome ? ink : cyan)
    context.setFillColor((monochrome && !dark ? NSColor.white : navy).cgColor)
    context.move(to: .init(x: 232, y: 269))
    context.addQuadCurve(to: .init(x: 241, y: 264), control: .init(x: 232, y: 259))
    context.addLine(to: .init(x: 283, y: 287))
    context.addQuadCurve(to: .init(x: 283, y: 299), control: .init(x: 293, y: 293))
    context.addLine(to: .init(x: 241, y: 323))
    context.addQuadCurve(to: .init(x: 232, y: 319), control: .init(x: 232, y: 329))
    context.closePath(); context.fillPath()
    rectangle(context, .init(x: 68, y: 354, width: 376, height: 50), radius: 16, color: ink)
}

func text(_ value: String, context: CGContext, x: CGFloat, y: CGFloat,
          size: CGFloat, weight: NSFont.Weight = .regular, tracking: CGFloat = 0, rounded: Bool = false, color: NSColor = navy) {
    let system = NSFont.systemFont(ofSize: size, weight: weight)
    let font = rounded ? system.fontDescriptor.withDesign(.rounded).flatMap { NSFont(descriptor: $0, size: size) } ?? system : system
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: true)
    (value as NSString).draw(at: .init(x: x, y: y), withAttributes: [.font: font, .foregroundColor: color, .kern: tracking])
    NSGraphicsContext.restoreGraphicsState()
}

func lockup(_ context: CGContext, monochrome: Bool = false, dark: Bool = false) {
    press(context, monochrome: monochrome, dark: dark)
    let ink: NSColor = dark ? .white : navy
    text("CamPresso", context: context, x: 500, y: 120, size: 154, weight: .black, tracking: -5, rounded: true, color: ink)
    text(tagline, context: context, x: 600, y: 305, size: 44, tracking: 10, color: ink)
}

func appIcon(_ context: CGContext) {
    rectangle(context, .init(x: 12, y: 12, width: 488, height: 488), radius: 110, color: navy)
    context.saveGState(); context.translateBy(x: 15, y: 59); context.scaleBy(x: 0.94, y: 0.94)
    press(context, dark: true); context.restoreGState()
}

func positioned(_ context: CGContext, x: CGFloat, y: CGFloat, scale: CGFloat, draw: () -> Void) {
    context.saveGState(); context.translateBy(x: x, y: y); context.scaleBy(x: scale, y: scale)
    draw(); context.restoreGState()
}

func export(_ name: String, width: Int, height: Int, artwork: Artwork, to root: URL? = nil) throws {
    let viewWidth: CGFloat = artwork == .board ? 1600 : artwork == .lockup || artwork == .darkLockup || artwork == .monochrome ? 1440 : 512
    let viewHeight: CGFloat = artwork == .board ? 1024 : 512
    guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        throw NSError(domain: "CamPresso.Brand", code: 1)
    }
    context.clear(.init(x: 0, y: 0, width: width, height: height))
    context.translateBy(x: 0, y: CGFloat(height)); context.scaleBy(x: CGFloat(width)/viewWidth, y: -CGFloat(height)/viewHeight)
    switch artwork {
    case .mark, .vertical: press(context)
    case .darkMark: press(context, dark: true)
    case .lockup: lockup(context)
    case .darkLockup: lockup(context, dark: true)
    case .monochrome: lockup(context, monochrome: true)
    case .icon: appIcon(context)
    case .board:
        rectangle(context, .init(x: 0, y: 0, width: 1600, height: 1024), radius: 0, color: .white)
        positioned(context, x: 80, y: 0, scale: 1) { lockup(context) }
        context.setStrokeColor(NSColor(srgbRed: 0.85, green: 0.88, blue: 0.89, alpha: 1).cgColor); context.setLineWidth(1)
        for (a, b) in [(CGPoint(x: 0, y: 540), CGPoint(x: 1600, y: 540)),
                       (CGPoint(x: 500, y: 575), CGPoint(x: 500, y: 990)),
                       (CGPoint(x: 1050, y: 575), CGPoint(x: 1050, y: 990))] {
            context.move(to: a); context.addLine(to: b); context.strokePath()
        }
        text("APP ICON", context: context, x: 70, y: 575, size: 18, tracking: 4)
        text("MONOCHROME", context: context, x: 545, y: 575, size: 18, tracking: 4)
        text("ON DARK", context: context, x: 1095, y: 575, size: 18, tracking: 4)
        positioned(context, x: 83, y: 630, scale: 0.67) { appIcon(context) }
        positioned(context, x: 525, y: 720, scale: 0.36) { lockup(context, monochrome: true) }
        rectangle(context, .init(x: 1095, y: 620, width: 450, height: 350), radius: 28, color: navy)
        positioned(context, x: 1110, y: 620, scale: 0.78) { press(context, dark: true) }
    }
    guard let image = context.makeImage(), let bytes = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
        throw NSError(domain: "CamPresso.Brand", code: 2)
    }
    try bytes.write(to: (root ?? destination).appendingPathComponent(name), options: .atomic)
}

try svg("campresso-mark.svg", body: geometry())
try svg("campresso-mark-dark.svg", body: geometry(dark: true))
try svg("campresso-lockup.svg", width: 1440, body: geometry()+lettering())
try svg("campresso-lockup-dark.svg", width: 1440, body: geometry(dark: true)+lettering(dark: true))
try svg("campresso-monochrome.svg", width: 1440, body: geometry(monochrome: true)+lettering())
// Retain the vertical filename as a compatible alias of the primary mark.
try svg("campresso-vertical.svg", body: geometry())
try svg("campresso-icon.svg", body: iconGeometry)
try export("campresso-mark.png", width: 1024, height: 1024, artwork: .mark)
try export("campresso-mark-dark.png", width: 1024, height: 1024, artwork: .darkMark)
try export("campresso-lockup.png", width: 2880, height: 1024, artwork: .lockup)
try export("campresso-lockup-dark.png", width: 2880, height: 1024, artwork: .darkLockup)
try export("campresso-monochrome.png", width: 2880, height: 1024, artwork: .monochrome)
try export("campresso-vertical.png", width: 1024, height: 1024, artwork: .vertical)
try export("campresso-icon.png", width: 1024, height: 1024, artwork: .icon)
try export("campresso-icon-32.png", width: 32, height: 32, artwork: .icon)
try export("campresso-brand-board.png", width: 1600, height: 1024, artwork: .board)

let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("campresso-icon-"+UUID().uuidString)
let iconset = temporary.appendingPathComponent("CamPresso.iconset")
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: temporary) }
for size in [16, 32, 128, 256, 512] {
    try export("icon_\(size)x\(size).png", width: size, height: size, artwork: .icon, to: iconset)
    try export("icon_\(size)x\(size)@2x.png", width: size*2, height: size*2, artwork: .icon, to: iconset)
}
func packed(_ value: UInt32) -> Data { var big = value.bigEndian; return withUnsafeBytes(of: &big) { Data($0) } }
var entries = Data()
for (kind, name) in [("icp4", "icon_16x16.png"), ("icp5", "icon_32x32.png"), ("icp6", "icon_32x32@2x.png"),
                     ("ic07", "icon_128x128.png"), ("ic08", "icon_256x256.png"), ("ic09", "icon_512x512.png"),
                     ("ic10", "icon_512x512@2x.png"), ("ic11", "icon_16x16@2x.png"), ("ic12", "icon_32x32@2x.png"),
                     ("ic13", "icon_128x128@2x.png"), ("ic14", "icon_256x256@2x.png")] {
    let bytes = try Data(contentsOf: iconset.appendingPathComponent(name))
    entries.append(Data(kind.utf8)); entries.append(packed(UInt32(bytes.count+8))); entries.append(bytes)
}
var icns = Data("icns".utf8); icns.append(packed(UInt32(entries.count+8))); icns.append(entries)
try icns.write(to: destination.appendingPathComponent("CamPresso.icns"), options: .atomic)
print("CamPresso SVG, PNG and ICNS assets exported to \(destination.path)")
