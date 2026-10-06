import IDIntelKit
import SwiftData
import SwiftUI

struct SettingsView: View {
    var body: some View {
        TabView {
            GeneralSettingsView()
                .tabItem { Label("General", systemImage: "gearshape") }
            JournalSettingsView()
                .tabItem { Label("Journals", systemImage: "books.vertical") }
        }
    }
}

/// Engine location and health. The app reads the Python engine's database and
/// its `config/settings.yaml`; this pane is where those paths are visible and
/// fixable — the checkout has moved once already (macOS TCC forced it out of
/// ~/Documents), so this must never be a hardcoded mystery.
struct GeneralSettingsView: View {
    @Environment(\.modelContext) private var context
    @Environment(ImportController.self) private var importer

    var body: some View {
        Form {
            Section("Engine") {
                LabeledContent("Database") {
                    Text(ImportController.databasePath)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                        .lineLimit(2)
                        .truncationMode(.middle)
                }
                LabeledContent("") {
                    Button("Choose Engine Folder…") { chooseFolder() }
                }
                healthRow("Engine database",
                          ok: FileManager.default.fileExists(atPath: ImportController.databasePath))
                healthRow("settings.yaml (Zotero, tuning)",
                          ok: FileManager.default.fileExists(atPath: AppPaths.settingsFile.path))
            }

            Section("Import") {
                LabeledContent("Status") { statusText }
                LabeledContent("") {
                    Button("Import Now") {
                        Task { await importer.refresh(container: context.container) }
                    }
                }
                Text("The app imports automatically when the engine finishes a run.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 520, height: 360)
    }

    private func healthRow(_ label: String, ok: Bool) -> some View {
        LabeledContent(label) {
            Label(ok ? "Found" : "Missing",
                  systemImage: ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(ok ? .green : .red)
                .font(.callout)
        }
    }

    @ViewBuilder
    private var statusText: some View {
        switch importer.status {
        case .idle: Text("Idle")
        case .running: Text("Importing…")
        case let .done(inserted, updated, at):
            Text("+\(inserted) new, \(updated) refreshed · \(at.formatted(date: .abbreviated, time: .shortened))")
        case let .failed(message):
            Text(message).foregroundStyle(.red)
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.prompt = "Use Folder"
        panel.message = "Select the ID Intelligence engine folder (the one containing data/idintel.db)"
        guard panel.runModal() == .OK, let folder = panel.url else { return }
        let db = folder.appending(path: "data/idintel.db")
        importer.setDatabasePath(db.path, container: context.container)
    }
}
