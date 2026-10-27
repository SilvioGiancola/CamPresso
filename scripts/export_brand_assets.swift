// Deterministic vector/raster brand exports. Does not load the app or Photos.
import AppKit
import CoreGraphics
import Foundation

let destination = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "assets/brand")
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
let espresso = NSColor(srgbRed: 43/255, green: 26/255, blue: 22/255, alpha: 1)
let orange = NSColor(srgbRed: 242/255, green: 122/255, blue: 42/255, alpha: 1)
let cream = NSColor(srgbRed: 1, green: 244/255, blue: 230/255, alpha: 1)
let geometry = """
<g fill="#2B1A16">
  <rect x="108" y="72" width="28" height="352" rx="14"/>
  <rect x="376" y="72" width="28" height="352" rx="14"/>
  <rect x="108" y="72" width="296" height="34" rx="12"/>
  <rect x="240" y="100" width="32" height="88" rx="6"/>
  <rect x="168" y="184" width="176" height="28" rx="12"/>
  <rect x="108" y="404" width="296" height="30" rx="12"/>
</g>
<g id="downward-motion-lines" stroke="#F27A2A" stroke-width="8" stroke-linecap="round">
  <path d="M216 238V262"/><path d="M256 228V262"/><path d="M296 238V262"/>
</g>
<rect x="158" y="290" width="196" height="90" rx="19" fill="#F27A2A"/>
<path d="M242 312L242 358L279 335Z" fill="#FFF4E6"/>
"""
let mark = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-labelledby="title desc">
<title id="title">CamPresso</title><desc id="desc">A vertical press compresses a video tile, with three downward motion lines.</desc>
\(geometry)
</svg>
"""
let lockup = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 512" role="img" aria-label="CamPresso — Action, packed lighter">
\(geometry)
<text x="490" y="246" fill="#2B1A16" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="110" font-weight="600" letter-spacing="-3">CamPresso</text>
<text x="496" y="314" fill="#2B1A16" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="36">Action, packed lighter</text>
</svg>
"""
try mark.write(to: destination.appendingPathComponent("campresso-mark.svg"), atomically: true, encoding: .utf8)
try lockup.write(to: destination.appendingPathComponent("campresso-lockup.svg"), atomically: true, encoding: .utf8)

func rectangle(_ context: CGContext, _ rect: CGRect, radius: CGFloat, color: NSColor) {
    context.setFillColor(color.cgColor)
    context.addPath(CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil))
    context.fillPath()
}
func press(_ context: CGContext) {
    for (x,y,w,h,r) in [(108.0,72.0,28.0,352.0,14.0),(376,72,28,352,14),(108,72,296,34,12),
                        (240,100,32,88,6),(168,184,176,28,12),(108,404,296,30,12)] {
        rectangle(context, .init(x:x,y:y,width:w,height:h), radius:r, color:espresso)
    }
    context.setStrokeColor(orange.cgColor); context.setLineWidth(8); context.setLineCap(.round)
    for (x,y) in [(216.0,238.0),(256,228),(296,238)] {
        context.move(to:.init(x:x,y:y));context.addLine(to:.init(x:x,y:262));context.strokePath()
    }
    rectangle(context, .init(x:158,y:290,width:196,height:90), radius:19, color:orange)
    context.setFillColor(cream.cgColor);context.move(to:.init(x:242,y:312))
    context.addLine(to:.init(x:242,y:358));context.addLine(to:.init(x:279,y:335));context.closePath();context.fillPath()
}
func export(_ name: String, width: Int, height: Int, icon: Bool = false, wordmark: Bool = false, to root: URL? = nil) throws {
    guard let context = CGContext(data:nil,width:width,height:height,bitsPerComponent:8,bytesPerRow:0,
                                  space:CGColorSpace(name:CGColorSpace.sRGB)!,bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue) else {
        throw NSError(domain:"CamPresso.Brand",code:1)
    }
    context.clear(.init(x:0,y:0,width:width,height:height))
    context.translateBy(x:0,y:CGFloat(height));context.scaleBy(x:CGFloat(width)/(wordmark ? 1200 : 512),y: -CGFloat(height)/512)
    if icon {
        rectangle(context,.init(x:12,y:12,width:488,height:488),radius:110,color:cream)
        context.saveGState();context.translateBy(x: -42,y: -44);context.scaleBy(x:1.16,y:1.16);press(context);context.restoreGState()
    } else { press(context) }
    if wordmark {
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext:context, flipped:true)
        ("CamPresso" as NSString).draw(at:.init(x:490,y:132),withAttributes:[.font:NSFont.systemFont(ofSize:110,weight:.semibold),.foregroundColor:espresso,.kern: -3])
        ("Action, packed lighter" as NSString).draw(at:.init(x:496,y:278),withAttributes:[.font:NSFont.systemFont(ofSize:36),.foregroundColor:espresso])
        NSGraphicsContext.restoreGraphicsState()
    }
    guard let image = context.makeImage(), let bytes = NSBitmapImageRep(cgImage:image).representation(using:.png,properties:[:]) else { throw NSError(domain:"CamPresso.Brand",code:2) }
    try bytes.write(to:(root ?? destination).appendingPathComponent(name),options:.atomic)
}
try export("campresso-mark.png",width:1024,height:1024)
try export("campresso-lockup.png",width:2400,height:1024,wordmark:true)
try export("campresso-icon.png",width:1024,height:1024,icon:true)
try export("campresso-icon-32.png",width:32,height:32,icon:true)
let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("campresso-icon-"+UUID().uuidString)
let iconset = temporary.appendingPathComponent("CamPresso.iconset")
try FileManager.default.createDirectory(at:iconset,withIntermediateDirectories:true)
defer { try? FileManager.default.removeItem(at:temporary) }
for size in [16,32,128,256,512] {
    try export("icon_\(size)x\(size).png",width:size,height:size,icon:true,to:iconset)
    try export("icon_\(size)x\(size)@2x.png",width:size*2,height:size*2,icon:true,to:iconset)
}
// ICNS is a big-endian container of PNG representations. Writing it directly
// also works on build hosts that disallow iconutil's nested sandbox.
func packed(_ value: UInt32) -> Data { var big = value.bigEndian; return withUnsafeBytes(of:&big) { Data($0) } }
var entries = Data()
for (kind,name) in [("icp4","icon_16x16.png"),("icp5","icon_32x32.png"),("icp6","icon_32x32@2x.png"),
                    ("ic07","icon_128x128.png"),("ic08","icon_256x256.png"),("ic09","icon_512x512.png"),
                    ("ic10","icon_512x512@2x.png"),("ic11","icon_16x16@2x.png"),("ic12","icon_32x32@2x.png"),
                    ("ic13","icon_128x128@2x.png"),("ic14","icon_256x256@2x.png")] {
    let bytes = try Data(contentsOf:iconset.appendingPathComponent(name))
    entries.append(Data(kind.utf8));entries.append(packed(UInt32(bytes.count+8)));entries.append(bytes)
}
var icns = Data("icns".utf8);icns.append(packed(UInt32(entries.count+8)));icns.append(entries)
try icns.write(to:destination.appendingPathComponent("CamPresso.icns"),options:.atomic)
print("CamPresso SVG, PNG and ICNS assets exported to \(destination.path)")
