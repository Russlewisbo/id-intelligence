import IDIntelKit
import SwiftData
import SwiftUI

/// Sidebar scopes. `digest` mirrors the Python engine's daily gate
/// (report.py): ranked journal + (topical hit OR score above the keep floor).
enum Scope: String, CaseIterable, Identifiable {
    case digest = "Digest"
    case starred = "Starred"
    case appraised = "Appraised"
    case all = "All Papers"

    var id: String { rawValue }

    var symbol: String {
        switch self {
        case .digest: "doc.text.image"
        case .starred: "star.fill"
        case .appraised: "sparkles"
        case .all: "tray.full"
        }
    }
}

struct ContentView: View {
    @Environment(\.modelContext) private var context
    private var container: ModelContainer { context.container }

    @Query(sort: \Paper.score, order: .reverse) private var papers: [Paper]

    @State private var scope: Scope = .digest
    @State private var selected: Paper?
    @State private var search = ""
    @State private var importer = ImportController()

    /// The engine's daily keep floor (settings.yaml `report.daily_keep_score`).
    /// Duplicated here until the Swift engine port reads the shared YAML.
    private let keepFloor = 25.0

    var body: some View {
        NavigationSplitView {
            List(Scope.allCases, selection: $scope) { s in
                Label(s.rawValue, systemImage: s.symbol).tag(s)
            }
            .navigationSplitViewColumnWidth(min: 170, ideal: 190)
        } content: {
            paperList
                .navigationSplitViewColumnWidth(min: 330, ideal: 400)
        } detail: {
            if let selected {
                PaperDetailView(paper: selected)
            } else {
                ContentUnavailableView("Select a paper", systemImage: "doc.text")
            }
        }
        .searchable(text: $search, placement: .sidebar, prompt: "Title, journal, topic")
        .navigationTitle("ID Intelligence")
        .toolbar {
            ToolbarItem(placement: .status) { statusLabel }
            ToolbarItem {
                Button("Refresh", systemImage: "arrow.clockwise") {
                    Task { await importer.refresh(container: container) }
                }
                .disabled(importer.status == .running)
            }
        }
        .task {
            if papers.isEmpty { await importer.refresh(container: container) }
        }
    }

    // ------------------------------------------------------------ list

    private var visiblePapers: [Paper] {
        papers.filter { paper in
            switch scope {
            case .digest: passesDigestGate(paper)
            case .starred: paper.starred
            case .appraised: paper.appraisalJSON != nil
            case .all: true
            }
        }
        .filter { matchesSearch($0) }
    }

    private var paperList: some View {
        List(selection: $selected) {
            ForEach(prioritySections, id: \.0) { label, group in
                Section("\(label) · \(group.count)") {
                    ForEach(group) { paper in
                        PaperRow(paper: paper).tag(paper)
                    }
                }
            }
        }
        .listStyle(.inset)
        .onChange(of: selected) { _, paper in
            // Opening a paper marks it read — state the HTML digest never had.
            if let paper, paper.readAt == nil {
                paper.readAt = .now
            }
        }
    }

    /// Same grouping as the HTML digest: critical → high → medium → low.
    private var prioritySections: [(String, [Paper])] {
        let order = ["critical", "high", "medium", "low"]
        let byPriority = Dictionary(grouping: visiblePapers, by: \.priority)
        return order.compactMap { key in
            guard let group = byPriority[key], !group.isEmpty else { return nil }
            return (key.capitalized, group)
        }
    }

    private func passesDigestGate(_ paper: Paper) -> Bool {
        guard let tier = paper.journalTier, tier != "Unranked" else { return false }
        return paper.topical || paper.score >= keepFloor
    }

    private func matchesSearch(_ paper: Paper) -> Bool {
        guard !search.isEmpty else { return true }
        let haystack = "\(paper.title) \(paper.journal ?? "") \(paper.appraisal?.topics.joined(separator: " ") ?? "")"
        return haystack.localizedCaseInsensitiveContains(search)
    }

    @ViewBuilder
    private var statusLabel: some View {
        switch importer.status {
        case .idle:
            Text("\(papers.count) papers")
        case .running:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Importing…")
            }
        case let .done(inserted, updated, at):
            Text("\(papers.count) papers · +\(inserted) new, \(updated) refreshed at \(at.formatted(date: .omitted, time: .shortened))")
        case let .failed(message):
            Label(message, systemImage: "exclamationmark.triangle")
                .foregroundStyle(.red)
                .help(message)
        }
    }
}
