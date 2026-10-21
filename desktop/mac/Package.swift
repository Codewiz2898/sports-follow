// swift-tools-version: 6.0
// Slam for Mac: record a game's window, keep the last 30 s, cut it into a shareable clip, publish it to Slam.
// Built with the Command Line Tools alone (no Xcode): see README.md.
import PackageDescription

let package = Package(
    name: "Slam",
    platforms: [.macOS("15.2")],
    targets: [
        .executableTarget(name: "Slam", path: "Sources/Slam"),
        .testTarget(name: "SlamTests", dependencies: ["Slam"], path: "Tests/SlamTests"),
    ],
    swiftLanguageModes: [.v5]
)
