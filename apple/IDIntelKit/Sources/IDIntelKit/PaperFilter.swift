import Foundation
import SwiftData

/// The sidebar scopes. `digest` mirrors the Python engine's daily gate
/// (report.py): ranked journal + (topical hit OR score above the keep floor).
public enum PaperScope: String, CaseIterable, Identifiable, Sendable {
    case today = "Today"
    case digest = "Digest"
    case starred = "Starred"
    case appraised = "Appraised"
    case all = "All Papers"

    public var id: String { rawValue }

    public var symbol: String {
        switch self {
        case .today: "sunrise.fill"
        case .digest: "doc.text.image"
        case .starred: "star.fill"
        case .appraised: "sparkles"
        case .all: "tray.full"
        }
    }
}

/// Builds the SwiftData predicates behind every list in the app.
///
/// Filtering MUST happen in SQLite, never over an in-memory `[Paper]`: each
/// SwiftData property read goes through observation and faulting machinery,
/// so scanning the whole store in Swift on every render froze the UI once the
/// store passed ~15k papers. With these predicates only the rows a list
/// actually shows are ever materialised.
public struct PaperFilter: Equatable, Sendable {
    public var scope: PaperScope
    public var search: String
    /// Journals unchecked in Settings → Journals; vetoed from Today/Digest.
    public var excludedJournals: [String]
    /// The engine's daily keep floor (settings.yaml `report.daily_keep_score`).
    public var keepFloor: Double
    /// Start of the Today window (papers first collected after this).
    public var todayCutoff: Date

    /// Display order of the priority bands, matching the HTML digest.
    public static let priorities = ["critical", "high", "medium", "low"]

    /// Rows per priority band in All Papers when not searching — the store
    /// holds tens of thousands of papers, far more than a list can usefully
    /// show; searching lifts the cap.
    public static let allScopeLimit = 300

    public init(scope: PaperScope, search: String = "", excludedJournals: [String] = [],
                keepFloor: Double = 25, now: Date = .now) {
        self.scope = scope
        self.search = search.trimmingCharacters(in: .whitespaces)
        self.excludedJournals = excludedJournals.sorted()
        self.keepFloor = keepFloor
        self.todayCutoff = now.addingTimeInterval(-24 * 3600)
    }

    /// Row cap for a band's fetch, or nil for no cap.
    public var fetchLimit: Int? {
        scope == .all && search.isEmpty ? Self.allScopeLimit : nil
    }

    /// Papers in this scope matching the search, optionally one priority band.
    public func predicate(priority: String? = nil) -> Predicate<Paper> {
        let scopeMatch = scopePredicate()
        let searchMatch = searchPredicate()
        let band = priority ?? ""
        let anyBand = priority == nil
        return #Predicate<Paper> { paper in
            scopeMatch.evaluate(paper)
                && searchMatch.evaluate(paper)
                && (anyBand || paper.priority == band)
        }
    }

    /// Today's papers not yet opened — the sidebar badge.
    public func unreadTodayPredicate() -> Predicate<Paper> {
        var today = self
        today.scope = .today
        let todayMatch = today.scopePredicate()
        return #Predicate<Paper> { paper in
            todayMatch.evaluate(paper) && paper.readAt == nil
        }
    }

    // ------------------------------------------------------------ pieces

    private func digestGate() -> Predicate<Paper> {
        let floor = keepFloor
        // Optional elements so the comparison is a plain `journal IN {…}`:
        // `journal ?? ""` cannot be generated as SQL and aborts the fetch.
        let excluded: [String?] = excludedJournals
        return #Predicate<Paper> { paper in
            paper.journalTier != nil
                && paper.journalTier != "Unranked"
                && (paper.topical || paper.score >= floor)
                && !excluded.contains(paper.journal)
        }
    }

    private func scopePredicate() -> Predicate<Paper> {
        switch scope {
        case .today:
            let gate = digestGate()
            let cutoff = todayCutoff
            return #Predicate<Paper> { paper in
                paper.firstSeen > cutoff && gate.evaluate(paper)
            }
        case .digest:
            return digestGate()
        case .starred:
            return #Predicate<Paper> { paper in paper.starred }
        case .appraised:
            return #Predicate<Paper> { paper in paper.appraisalJSON != nil }
        case .all:
            return #Predicate<Paper> { _ in true }
        }
    }

    /// Title, journal or appraisal topic — via `Paper.searchIndex`, a single
    /// non-optional column, because optional-coalescing can't become SQL.
    private func searchPredicate() -> Predicate<Paper> {
        guard !search.isEmpty else { return #Predicate<Paper> { _ in true } }
        let q = search
        return #Predicate<Paper> { paper in
            paper.searchIndex.localizedStandardContains(q)
        }
    }
}
