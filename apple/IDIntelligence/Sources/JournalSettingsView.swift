import IDIntelKit
import SwiftData
import SwiftUI

/// The journal picker (Settings, ⌘,): every journal seen in the store,
/// grouped by tier, with a checkbox controlling whether its papers appear in
/// the Digest scope. Unchecking inserts an `ExcludedJournal` row; the list is
/// data-driven, so newly collected venues show up here automatically.
struct JournalSettingsView: View {
    @Environment(\.modelContext) private var context
    @Query private var papers: [Paper]
    @Query private var exclusions: [ExcludedJournal]

    @State private var search = ""

    private static let tierOrder =
        ["Top general", "Core ID", "Specialist", "Agency / society", "Unranked"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Checked journals appear in the Digest. All Papers always shows everything.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .padding()

            TextField("Filter journals", text: $search)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal)

            List {
                ForEach(sections, id: \.tier) { section in
                    Section("\(section.tier) · \(section.journals.count)") {
                        ForEach(section.journals, id: \.name) { journal in
                            Toggle(isOn: binding(for: journal.name)) {
                                HStack {
                                    Text(journal.name).lineLimit(1)
                                    Spacer()
                                    Text("\(journal.count)")
                                        .font(.caption.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .toggleStyle(.checkbox)
                        }
                    }
                }
            }
            .listStyle(.inset)

            HStack {
                Text("\(exclusions.count) excluded")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Include All") {
                    exclusions.forEach { context.delete($0) }
                }
                .disabled(exclusions.isEmpty)
            }
            .padding()
        }
        .frame(width: 520, height: 480)
    }

    // ------------------------------------------------------------- data

    private struct JournalEntry {
        let name: String
        let tier: String
        let count: Int
    }

    private var sections: [(tier: String, journals: [JournalEntry])] {
        var byName: [String: (tier: String, count: Int)] = [:]
        for paper in papers {
            guard let name = paper.journal else { continue }
            let tier = paper.journalTier ?? "Unranked"
            let entry = byName[name] ?? (tier, 0)
            byName[name] = (entry.tier, entry.count + 1)
        }
        let entries = byName.map { JournalEntry(name: $0.key, tier: $0.value.tier, count: $0.value.count) }
            .filter { search.isEmpty || $0.name.localizedCaseInsensitiveContains(search) }
        return Self.tierOrder.compactMap { tier in
            let group = entries.filter { $0.tier == tier }
                .sorted { ($0.count, $1.name) > ($1.count, $0.name) }
            return group.isEmpty ? nil : (tier, group)
        }
    }

    private func binding(for name: String) -> Binding<Bool> {
        Binding(
            get: { !exclusions.contains { $0.name == name } },
            set: { include in
                if include {
                    exclusions.filter { $0.name == name }.forEach { context.delete($0) }
                } else if !exclusions.contains(where: { $0.name == name }) {
                    context.insert(ExcludedJournal(name: name))
                }
            })
    }
}
