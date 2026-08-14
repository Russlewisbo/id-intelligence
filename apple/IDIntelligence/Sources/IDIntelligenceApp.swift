import IDIntelKit
import SwiftData
import SwiftUI

@main
struct IDIntelligenceApp: App {
    let container: ModelContainer

    init() {
        do {
            container = try ModelContainer(for: Paper.self)
        } catch {
            fatalError("Cannot open the local store: \(error)")
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .modelContainer(container)
    }
}

/// Bridges the Python engine's SQLite database into the app's store.
/// Phase 2 of the plan: the engine remains the system of record; the app
/// re-imports on launch and on demand until the pipeline is ported.
@Observable
@MainActor
final class ImportController {
    enum Status: Equatable {
        case idle
        case running
        case done(inserted: Int, updated: Int, at: Date)
        case failed(String)
    }

    var status: Status = .idle

    /// Default engine location; overridable in the UI via UserDefaults.
    static var databasePath: String {
        UserDefaults.standard.string(forKey: "legacyDBPath")
            ?? NSHomeDirectory() + "/id-intelligence/data/idintel.db"
    }

    func refresh(container: ModelContainer) async {
        guard status != .running else { return }
        status = .running
        let path = Self.databasePath
        do {
            // Import on a background context; the main context's @Query views
            // pick up the saved changes automatically.
            let summary = try await Task.detached(priority: .utility) {
                let context = ModelContext(container)
                context.autosaveEnabled = false
                return try LegacyStoreImporter(databasePath: path).run(into: context)
            }.value
            status = .done(inserted: summary.inserted, updated: summary.updated, at: .now)
        } catch {
            status = .failed("\(error)")
        }
    }
}
