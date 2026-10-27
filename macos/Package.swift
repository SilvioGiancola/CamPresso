// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "VideoQueue",
    platforms: [.macOS("26.0")],
    products: [.executable(name: "VideoQueue", targets: ["VideoQueue"])],
    targets: [.executableTarget(name: "VideoQueue")],
    swiftLanguageModes: [.v5]
)
