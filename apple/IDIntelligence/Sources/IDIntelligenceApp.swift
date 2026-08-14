import IDIntelKit
import SwiftData
import SwiftUI

@main
struct IDIntelligenceApp: App {
    let container: ModelContainer

    init() {
        do {
            container = try ModelContainer(for: Paper.self, ExcludedJournal.self)
        } catch {
            fatalError("Cannot open the local store: \(error)")
        }
    }

    @State private var importer = ImportController()

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .modelContainer(container)
        .environment(importer)

        // App menu → Settings… (⌘,): engine location + the journal picker.
        Settings {
            SettingsView()
        }
        .modelContainer(container)
        .environment(importer)
    }
}

/// Locations shared with the Python engine. Everything derives from the
/// engine checkout so `config/settings.yaml` stays the single config surface.
enum AppPaths {
    static let collectionKeyDefault = "zoteroCollectionKey"

    /// `<engine>/data/idintel.db` → `<engine>/config/settings.yaml`.
    static var settingsFile: URL {
        URL(fileURLWithPath: ImportController.databasePath)
            .deletingLastPathComponent()   // data/
            .deletingLastPathComponent()   // engine root
            .appending(path: "config/settings.yaml")
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

    /// Default engine location; overridable via UserDefaults. Nonisolated:
    /// UserDefaults is thread-safe and nonisolated AppPaths derives from this.
    nonisolated static var databasePath: String {
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

    // ------------------------------------------------------- auto-import

    private var watcher: DispatchSourceFileSystemObject?
    private var pendingRefresh: Task<Void, Never>?
    private var started = false

    /// Launch sequence: import whatever the engine produced while the app was
    /// closed, then watch the database so scheduled runs land automatically.
    func startup(container: ModelContainer) async {
        guard !started else { return }
        started = true
        await refresh(container: container)
        startWatching(container: container)
    }

    /// Watches the engine's SQLite file. The engine writes in place over
    /// several minutes during a run, so imports are debounced until the file
    /// has been quiet for a few seconds; the importer is idempotent, so an
    /// occasional mid-run import is harmless.
    func startWatching(container: ModelContainer) {
        stopWatching()
        let fd = open(Self.databasePath, O_EVTONLY)
        guard fd >= 0 else { return }   // engine missing — Settings shows it
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd,
            eventMask: [.write, .extend, .delete, .rename],
            queue: .main)
        source.setEventHandler { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                if source.data.contains(.delete) || source.data.contains(.rename) {
                    // File replaced: the fd tracks the old inode — rewatch.
                    self.startWatching(container: container)
                }
                self.scheduleRefresh(container: container)
            }
        }
        source.setCancelHandler { close(fd) }
        source.resume()
        watcher = source
    }

    func stopWatching() {
        watcher?.cancel()
        watcher = nil
        pendingRefresh?.cancel()
    }

    /// Point the app at a different engine checkout (Settings → General).
    func setDatabasePath(_ path: String, container: ModelContainer) {
        UserDefaults.standard.set(path, forKey: "legacyDBPath")
        startWatching(container: container)
        Task { await refresh(container: container) }
    }

    private func scheduleRefresh(container: ModelContainer) {
        pendingRefresh?.cancel()
        pendingRefresh = Task {
            try? await Task.sleep(for: .seconds(8))
            guard !Task.isCancelled else { return }
            await refresh(container: container)
        }
    }
}
