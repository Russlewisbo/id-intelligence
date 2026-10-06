import Foundation
import SwiftData
import XCTest
@testable import IDIntelKit

/// The SQLite-side predicates must select exactly what the original in-memory
/// filters selected — verified against the real engine database. One shared
/// import keeps the suite fast.
final class PaperFilterTests: XCTestCase {

    nonisolated(unsafe) static var container: ModelContainer?

    override class func setUp() {
        super.setUp()
        let path = LegacyStoreImporterTests.databasePath
        guard FileManager.default.fileExists(atPath: path) else { return }
        let container = try! ModelContainer(
            for: Paper.self,
            configurations: ModelConfiguration(isStoredInMemoryOnly: true))
        try! LegacyStoreImporter(databasePath: path).run(into: ModelContext(container))
        Self.container = container
    }

    private func context() throws -> ModelContext {
        let container = try XCTUnwrap(Self.container, "no legacy database")
        return ModelContext(container)
    }

    // The original ContentView logic, kept here as the reference oracle.
    private func referenceGate(_ p: Paper, excluded: Set<String>, floor: Double = 25) -> Bool {
        guard let tier = p.journalTier, tier != "Unranked" else { return false }
        if let j = p.journal, excluded.contains(j) { return false }
        return p.topical || p.score >= floor
    }

    private func count(_ filter: PaperFilter, priority: String? = nil,
                       in ctx: ModelContext) throws -> Int {
        try ctx.fetchCount(FetchDescriptor(predicate: filter.predicate(priority: priority)))
    }

    func testScopesMatchReferenceFilters() throws {
        let ctx = try context()
        let all = try ctx.fetch(FetchDescriptor<Paper>())
        let excluded: Set<String> = ["J Fungi (Basel)", "Mycoses"]
        let now = Date.now
        let cutoff = now.addingTimeInterval(-24 * 3600)

        let expected: [PaperScope: Int] = [
            .digest: all.count { referenceGate($0, excluded: excluded) },
            .today: all.count { $0.firstSeen > cutoff && referenceGate($0, excluded: excluded) },
            .appraised: all.count { $0.appraisalJSON != nil },
            .starred: all.count { $0.starred },
            .all: all.count,
        ]
        for (scope, want) in expected {
            let filter = PaperFilter(scope: scope, excludedJournals: Array(excluded), now: now)
            XCTAssertEqual(try count(filter, in: ctx), want, "\(scope) count")
        }
        XCTAssertGreaterThan(expected[.digest]!, 500, "sanity: digest is substantial")
    }

    func testPriorityBandsPartitionTheScope() throws {
        let ctx = try context()
        let filter = PaperFilter(scope: .digest)
        let whole = try count(filter, in: ctx)
        let bands = try PaperFilter.priorities.map { try count(filter, priority: $0, in: ctx) }
        XCTAssertEqual(bands.reduce(0, +), whole, "bands sum to the scope")
    }

    func testSearchMatchesTitleJournalAndTopics() throws {
        let ctx = try context()
        let all = try ctx.fetch(FetchDescriptor<Paper>())
        for term in ["aspergill", "Clin Infect Dis", "CMV", "stewardship"] {
            let want = all.count { p in
                p.title.localizedStandardContains(term)
                    || (p.journal ?? "").localizedStandardContains(term)
                    || (p.appraisal?.topics ?? []).contains { $0.localizedStandardContains(term) }
            }
            XCTAssertEqual(try count(PaperFilter(scope: .all, search: term), in: ctx),
                           want, "search \(term)")
        }
    }

    func testUnreadTodayExcludesReadPapers() throws {
        let ctx = try context()
        let filter = PaperFilter(scope: .today)
        let unread = FetchDescriptor(predicate: filter.unreadTodayPredicate())
        let before = try ctx.fetchCount(unread)
        let today = try ctx.fetch(FetchDescriptor(predicate: filter.predicate()))
        guard let target = today.first(where: { $0.readAt == nil }) else {
            throw XCTSkip("no unread papers collected in the last 24h")
        }
        target.readAt = .now
        XCTAssertEqual(try ctx.fetchCount(unread), before - 1)
    }

    /// The whole point: a band fetch over 15k papers is milliseconds.
    func testBandFetchIsFast() throws {
        let ctx = try context()
        let filter = PaperFilter(scope: .digest, search: "")
        let start = Date.now
        for band in PaperFilter.priorities {
            var d = FetchDescriptor(predicate: filter.predicate(priority: band),
                                    sortBy: [SortDescriptor(\Paper.score, order: .reverse)])
            d.fetchLimit = filter.fetchLimit
            _ = try ctx.fetch(d)
        }
        let elapsed = Date.now.timeIntervalSince(start)
        print("digest: 4 band fetches in \(Int(elapsed * 1000)) ms")
        XCTAssertLessThan(elapsed, 1.0)
    }
}
