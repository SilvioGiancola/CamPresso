import AppKit
import SwiftUI

enum Brand {
    static let tagline = "Action, packed lighter"
    // Keep these sRGB values aligned with docs/BRANDING.md and the asset exporter.
    static let background = Color(red: 7/255, green: 30/255, blue: 42/255) // #071E2A
    static let surface = Color(red: 18/255, green: 47/255, blue: 61/255) // #122F3D
    static let accent = Color(red: 1, green: 91/255, blue: 36/255) // #FF5B24
    static let cyan = Color(red: 176/255, green: 232/255, blue: 240/255) // #B0E8F0
    static let windowBackground = NSColor(srgbRed: 7/255, green: 30/255, blue: 42/255, alpha: 1)
    static var mark: NSImage? {
        Bundle.main.url(forResource: "campresso-mark-dark", withExtension: "png").flatMap { NSImage(contentsOf: $0) }
    }
    @MainActor static func about() {
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "CamPresso",
            .credits: NSAttributedString(string: tagline, attributes: [.font: NSFont.systemFont(ofSize: 14)]),
        ])
    }
}

struct BrandHeading: View {
    var body: some View {
        HStack(spacing: 10) {
            if let image = Brand.mark {
                Image(nsImage: image).resizable().scaledToFit().frame(width: 58, height: 58)
                    .background(Brand.background, in: RoundedRectangle(cornerRadius: 12))
            }
            VStack(alignment: .leading, spacing: 3) {
                Text("CamPresso").font(.system(size: 24, weight: .black, design: .rounded))
                Text(Brand.tagline).foregroundStyle(.secondary)
            }
        }
    }
}

private struct BrandWindowAppearance: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView { let view = NSView(); apply(view); return view }
    func updateNSView(_ view: NSView, context: Context) { apply(view) }
    private func apply(_ view: NSView) {
        DispatchQueue.main.async { [weak view] in
            guard let window = view?.window else { return }
            if window.appearance?.name != .darkAqua { window.appearance = NSAppearance(named: .darkAqua) }
            if window.backgroundColor != Brand.windowBackground { window.backgroundColor = Brand.windowBackground }
        }
    }
}

private struct BrandTheme: ViewModifier {
    func body(content: Content) -> some View {
        content.preferredColorScheme(.dark).tint(Brand.accent)
            .background(Brand.background).background(BrandWindowAppearance())
    }
}

extension View {
    func campressoTheme() -> some View { modifier(BrandTheme()) }
}
