import Foundation
import SwiftData
import XCTest
@testable import IDIntelKit

/// Integration tests against the real Python-engine database when present
/// (repo-relative `data/idintel.db`, overridable via `IDINTEL_DB`). Skipped
/// cleanly on machines without the data so the package always builds green.
final class LegacyStoreImporterTests: XCTestCase {

    static var databasePath: String {
        if let env = ProcessInfo.processInfo.environment["IDINTEL_DB"] { return env }
        // …/apple/IDIntelKit/Tests/IDIntelKitTests/ThisFile.swift → repo root
        return URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // strip file name → IDIntelKitTests/
            .deletingLastPathComponent()   // → Tests/
            .deletingLastPathComponent()   // → IDIntelKit/
            .deletingLastPathComponent()   // → apple/
            .deletingLastPathComponent()   // → repo root
            .appendingPathComponent("data/idintel.db").path
    }

    private func makeContext() throws -> ModelContext {
        let container = try ModelContainer(
            for: Paper.self,
            configurations: ModelConfiguration(isStoredInMemoryOnly: true))
        return ModelContext(container)
    }

    private func requireDatabase() throws -> String {
        let path = Self.databasePath
        try XCTSkipUnless(FileManager.default.fileExists(atPath: path),
                          "no legacy database at \(path)")
        return path
    }

    func testImportsRealDatabase() throws {
        let context = try makeContext()
        let summary = try LegacyStoreImporter(databasePath: requireDatabase())
            .run(into: context)

        XCTAssertGreaterThan(summary.inserted, 1000, "expected the full backfill")
        XCTAssertEqual(summary.updated, 0, "fresh store should only insert")

        // Field mapping spot-checks across the whole set.
        let papers = try context.fetch(FetchDescriptor<Paper>())
        XCTAssertEqual(papers.count, summary.inserted)
        XCTAssertTrue(papers.allSatisfy { $0.legacyID > 0 })
        XCTAssertTrue(papers.contains { !$0.authors.isEmpty }, "authors JSON decoded")
        XCTAssertTrue(papers.contains { $0.journalTier == "Core ID" }, "tiers imported")
        XCTAssertTrue(papers.contains { !$0.scoreBreakdown.isEmpty }, "breakdowns decoded")
        XCTAssertTrue(papers.contains { $0.firstSeen > .distantPast }, "ISO dates parsed")
    }

    func testAppraisalsDecode() throws {
        let context = try makeContext()
        try LegacyStoreImporter(databasePath: requireDatabase()).run(into: context)

        let appraised = try context.fetch(FetchDescriptor<Paper>())
            .filter { $0.appraisal != nil }
        XCTAssertGreaterThan(appraised.count, 10, "engine has appraised >10 records")
        for paper in appraised {
            let a = try XCTUnwrap(paper.appraisal)
            XCTAssertFalse(a.bottomLine.isEmpty)
            XCTAssertTrue((1...5).contains(a.stars), "stars in range for \(paper.legacyID)")
            XCTAssertTrue(["yes", "skim", "no"].contains(a.readFull))
        }
    }

    func testReimportIsIdempotentAndPreservesNativeState() throws {
        let path = try requireDatabase()
        let context = try makeContext()
        let importer = LegacyStoreImporter(databasePath: path)

        let first = try importer.run(into: context)

        // Simulate the app's value-add state between two engine runs.
        let papers = try context.fetch(FetchDescriptor<Paper>())
        let marked = try XCTUnwrap(papers.first)
        marked.starred = true
        marked.readAt = Date()
        try context.save()

        let second = try importer.run(into: context)
        XCTAssertEqual(second.inserted, 0, "no duplicates on re-import")
        XCTAssertEqual(second.updated, first.inserted, "every row refreshed")
        XCTAssertEqual(try context.fetch(FetchDescriptor<Paper>()).count, first.inserted)

        XCTAssertTrue(marked.starred, "starred survived re-import")
        XCTAssertNotNil(marked.readAt, "readAt survived re-import")
    }
}
