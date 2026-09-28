// swift-tools-version:5.9
// Roughcut, the Mac app around the fcpxml-roughcut engine. Built with the Command Line Tools alone
// (no Xcode project): app/build_app.sh turns the executable into Roughcut.app.
import PackageDescription

let package = Package(
    name: "Roughcut",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "Roughcut", path: "Sources/Roughcut")
    ]
)
