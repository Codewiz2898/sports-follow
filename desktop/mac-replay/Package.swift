// swift-tools-version: 6.0
// Throwaway spike (BHA-351's Mac twin): an instant-replay recorder for the Dota 2 window.
// Never merged; see README.md.
import PackageDescription

let package = Package(
    name: "SlamReplay",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(
            name: "SlamReplay",
            path: "Sources/SlamReplay"
        ),
    ],
    swiftLanguageModes: [.v5]
)
