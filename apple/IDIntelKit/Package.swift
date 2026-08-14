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
    dependencies: [
        // YAML: the engine's config/*.yaml stay the shared tuning surface
        // (plan D4) — Zotero credentials now, scoring rules with the port.
        .package(url: "https://github.com/jpsim/Yams.git", from: "5.0.0")
    ],
    targets: [
        .target(name: "IDIntelKit", dependencies: ["Yams"]),
        .testTarget(name: "IDIntelKitTests", dependencies: ["IDIntelKit"]),
    ]
)
