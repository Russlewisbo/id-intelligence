// swift-tools-version: 6.0
// IDIntelKit — shared core for the native ID Intelligence apps (macOS + iOS).
// Phase 1 of docs/swift-app-plan.md: SwiftData models mirroring the Python
// engine's SQLite schema, plus an importer that reads data/idintel.db so the
// Python pipeline keeps feeding the apps throughout the migration.
import PackageDescription

let package = Package(
    name: "IDIntelKit",
    platforms: [.macOS(.v15), .iOS(.v18)],
    products: [
        .library(name: "IDIntelKit", targets: ["IDIntelKit"])
    ],
    targets: [
        .target(name: "IDIntelKit"),
        .testTarget(name: "IDIntelKitTests", dependencies: ["IDIntelKit"]),
    ]
)
