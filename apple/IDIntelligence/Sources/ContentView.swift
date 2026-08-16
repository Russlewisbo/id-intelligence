import IDIntelKit
import SwiftData
import SwiftUI

/// Sidebar scopes. `digest` mirrors the Python engine's daily gate
/// (report.py): ranked journal + (topical hit OR score above the keep floor).
enum Scope: String, CaseIterable, Identifiable {
    case today = "Today"
    case digest = "Digest"
    case starred = "Starred"
    case appraised = "Appraised"
    case all = "All Papers"

    var id: String { rawValue }

    var symbol: String {
        switch self {
        case .today: "sunrise.fill"
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
    @Query private var exclusions: [ExcludedJournal]

    @State private var scope: Scope = .today
    @State private var selected: Paper?
    @State private var search = ""
    @Environment(ImportController.self) private var importer

    /// The engine's daily keep floor (settings.yaml `report.daily_keep_score`).
    /// Duplicated here until the Swift engine port reads the shared YAML.
    private let keepFloor = 25.0

    var body: some View {
        NavigationSplitView {
            List(Scope.allCases, selection: $scope) { s in
                Label(s.rawValue, systemImage: s.symbol)
                    .badge(s == .today ? unreadTodayCount : 0)
                    .tag(s)
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
            // Import anything the engine produced while the app was closed,
            // then file-watch so scheduled runs land without a manual Refresh.
            await importer.startup(container: container)
        }
    }

    // ------------------------------------------------------------ list

    private var visiblePapers: [Paper] {
        papers.filter { paper in
            switch scope {
            case .today: isFreshArrival(paper) && passesDigestGate(paper)
            case .digest: passesDigestGate(paper)
            case .starred: paper.starred
            case .appraised: paper.appraisalJSON != nil
            case .all: true
            }
        }
        .filter { matchesSearch($0) }
    }

    /// The morning-digest framing the HTML report had and the app lost: only
    /// papers the engine first collected in the last 24 hours. The 87 new
    /// papers of a given morning are invisible when sorted by score into
    /// 5,000+ older records; this scope is where "what's new today" lives.
    private func isFreshArrival(_ paper: Paper) -> Bool {
        paper.firstSeen > Date.now.addingTimeInterval(-24 * 3600)
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
        // Journals unchecked in Settings (⌘,) are vetoed whatever they score.
        if let journal = paper.journal,
           excludedJournals.contains(journal) { return false }
        return paper.topical || paper.score >= keepFloor
    }

    private var excludedJournals: Set<String> {
        Set(exclusions.map(\.name))
    }

    private var unreadTodayCount: Int {
        papers.count { isFreshArrival($0) && passesDigestGate($0) && $0.readAt == nil }
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
