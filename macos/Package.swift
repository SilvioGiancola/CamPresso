// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "CamPresso",
    platforms: [.macOS("26.0")],
    products: [.executable(name: "CamPresso", targets: ["CamPresso"])],
    targets: [.executableTarget(name: "CamPresso")],
    swiftLanguageModes: [.v5]
)
