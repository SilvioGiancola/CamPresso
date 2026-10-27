import AppKit
import SwiftUI

enum Brand {
    static let tagline = "Action, packed lighter"
    static let accent = Color(red: 242/255, green: 122/255, blue: 42/255)
    static var mark: NSImage? {
        Bundle.main.url(forResource: "campresso-mark", withExtension: "png").flatMap { NSImage(contentsOf: $0) }
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
                    .background(Color(red: 1, green: 244/255, blue: 230/255), in: RoundedRectangle(cornerRadius: 12))
            }
            VStack(alignment: .leading, spacing: 3) {
                Text("CamPresso").font(.title2.bold())
                Text(Brand.tagline).foregroundStyle(.secondary)
            }
        }
    }
}
