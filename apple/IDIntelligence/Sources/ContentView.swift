import Combine
import IDIntelKit
import SwiftData
import SwiftUI

struct ContentView: View {
    @Environment(\.modelContext) private var context
    private var container: ModelContainer { context.container }

    // Tiny table (the reader's unchecked journals) — fine to observe whole.
    @Query private var exclusions: [ExcludedJournal]

    @State private var scope: PaperScope = .today
    @State private var selected: Paper?
    @State private var search = ""
    @State private var unreadToday = 0
    @State private var totalPapers = 0
    @Environment(ImportController.self) private var importer

    /// The engine's daily keep floor (settings.yaml `report.daily_keep_score`).
    /// Duplicated here until the Swift engine port reads the shared YAML.
    private let keepFloor = 25.0

    private var filter: PaperFilter {
        PaperFilter(scope: scope, search: search,
                    excludedJournals: exclusions.map(\.name), keepFloor: keepFloor)
    }

    var body: some View {
        NavigationSplitView {
            List(PaperScope.allCases, selection: $scope) { s in
                Label(s.rawValue, systemImage: s.symbol)
                    .badge(s == .today ? unreadToday : 0)
                    .tag(s)
            }
            .navigationSplitViewColumnWidth(min: 170, ideal: 190)
        } content: {
            PaperListView(filter: filter, selected: $selected)
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
                .help("Import the engine's latest results (the engine itself runs on its own schedule)")
            }
        }
        .onChange(of: selected) { _, paper in
            // Opening a paper marks it read — state the HTML digest never had.
            if let paper, paper.readAt == nil {
                paper.readAt = .now
            }
        }
        // Counts are SQL COUNTs, refreshed when something is saved (an import
        // finishing, a paper read/unread, a journal unchecked) — never by
        // scanning the store during a render.
        .onReceive(NotificationCenter.default
            .publisher(for: ModelContext.didSave)
            .receive(on: RunLoop.main)) { _ in refreshCounts() }
        .onChange(of: exclusions.map(\.name)) { refreshCounts() }
        .task {
            refreshCounts()
            // Import anything the engine produced while the app was closed,
            // then file-watch so scheduled runs land without a manual Refresh.
            await importer.startup(container: container)
        }
    }

    private func refreshCounts() {
        unreadToday = (try? context.fetchCount(
            FetchDescriptor(predicate: filter.unreadTodayPredicate()))) ?? 0
        totalPapers = (try? context.fetchCount(FetchDescriptor<Paper>())) ?? 0
    }

    @ViewBuilder
    private var statusLabel: some View {
        switch importer.status {
        case .idle:
            Text("\(totalPapers.formatted()) papers")
        case .running:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Importing…")
            }
        case let .done(inserted, updated, at):
            Text("\(totalPapers.formatted()) papers · +\(inserted) new, \(updated) updated at \(at.formatted(date: .omitted, time: .shortened))")
        case let .failed(message):
            Label(message, systemImage: "exclamationmark.triangle")
                .foregroundStyle(.red)
                .help(message)
        }
    }
}

/// The paper list for one filter: one SQLite-backed query per priority band,
/// so only the rows on screen are ever loaded — never the whole store.
struct PaperListView: View {
    let filter: PaperFilter
    @Binding var selected: Paper?

    var body: some View {
        List(selection: $selected) {
            ForEach(PaperFilter.priorities, id: \.self) { band in
                PrioritySection(filter: filter, priority: band)
            }
        }
        .listStyle(.inset)
    }
}

private struct PrioritySection: View {
    @Query private var papers: [Paper]
    private let priority: String
    private let limit: Int?

    init(filter: PaperFilter, priority: String) {
        var descriptor = FetchDescriptor(
            predicate: filter.predicate(priority: priority),
            sortBy: [SortDescriptor(\Paper.score, order: .reverse)])
        descriptor.fetchLimit = filter.fetchLimit
        _papers = Query(descriptor)
        self.priority = priority
        self.limit = filter.fetchLimit
    }

    var body: some View {
        if !papers.isEmpty {
            Section(header) {
                ForEach(papers) { paper in
                    PaperRow(paper: paper).tag(paper)
                }
            }
        }
    }

    /// "Low · 300+" when All Papers hits its cap — search to see the rest.
    private var header: String {
        let capped = limit.map { papers.count >= $0 } ?? false
        return "\(priority.capitalized) · \(papers.count)\(capped ? "+ (search to narrow)" : "")"
    }
}
