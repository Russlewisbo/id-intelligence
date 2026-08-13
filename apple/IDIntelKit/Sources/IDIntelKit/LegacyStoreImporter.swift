import Foundation
import SQLite3
import SwiftData

/// Imports the Python engine's `data/idintel.db` into the SwiftData store.
///
/// Migration bridge (plan §3, Phase 1): the Python pipeline stays the system
/// of record while the native apps are built, so this importer must be safe to
/// run repeatedly — it upserts by `records.id` (`Paper.legacyID`), refreshes
/// engine-owned fields (score, priority, appraisal, Zotero state) and never
/// touches native-only state (`readAt`, `starred`). The legacy database is
/// opened read-only; the Python engine never sees a write from us.
public struct LegacyStoreImporter {

    public struct Summary: Sendable {
        public var inserted = 0
        public var updated = 0
        public var total: Int { inserted + updated }
    }

    public enum ImportError: Error, CustomStringConvertible {
        case cannotOpen(path: String, code: Int32)
        case queryFailed(String)

        public var description: String {
            switch self {
            case let .cannotOpen(path, code):
                return "cannot open legacy database at \(path) (sqlite error \(code))"
            case let .queryFailed(message):
                return "legacy database query failed: \(message)"
            }
        }
    }

    let databasePath: String

    public init(databasePath: String) {
        self.databasePath = databasePath
    }

    /// Runs the import into `context`. Returns counts of inserted/updated papers.
    @discardableResult
    public func run(into context: ModelContext) throws -> Summary {
        var db: OpaquePointer?
        // READONLY: the Python engine owns this file; we must never write it,
        // and an open must not create a stray empty database on a bad path.
        let flags = SQLITE_OPEN_READONLY
        let rc = sqlite3_open_v2(databasePath, &db, flags, nil)
        guard rc == SQLITE_OK, let db else {
            sqlite3_close(db)
            throw ImportError.cannotOpen(path: databasePath, code: rc)
        }
        defer { sqlite3_close(db) }

        let sql = """
            SELECT id, kind, doi, pmid, nct, title, abstract, authors, journal,
                   url, published, first_seen, last_seen, pub_types, sources,
                   score, score_breakdown, priority, summary, summary_at,
                   summary_model, summary_error, topical, journal_tier,
                   archived_at, zotero_key
              FROM records
            """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, let stmt else {
            throw ImportError.queryFailed(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }

        // One fetch of the existing mapping beats a per-row predicate fetch.
        let existing = try context.fetch(FetchDescriptor<Paper>(
            predicate: #Predicate { $0.legacyID > 0 }))
        var byLegacyID = Dictionary(existing.map { ($0.legacyID, $0) },
                                    uniquingKeysWith: { first, _ in first })

        var summary = Summary()
        while true {
            let step = sqlite3_step(stmt)
            if step == SQLITE_DONE { break }
            guard step == SQLITE_ROW else {
                throw ImportError.queryFailed(String(cString: sqlite3_errmsg(db)))
            }
            let row = Row(stmt: stmt)
            let legacyID = row.int64(0)
            let paper: Paper
            if let found = byLegacyID[legacyID] {
                paper = found
                summary.updated += 1
            } else {
                paper = Paper()
                paper.legacyID = legacyID
                context.insert(paper)
                byLegacyID[legacyID] = paper
                summary.inserted += 1
            }
            apply(row, to: paper)
        }

        try context.save()
        return summary
    }

    // ------------------------------------------------------------- mapping

    private func apply(_ row: Row, to paper: Paper) {
        paper.kind = row.text(1) ?? "article"
        paper.doi = row.text(2)
        paper.pmid = row.text(3)
        paper.nct = row.text(4)
        paper.title = row.text(5) ?? ""
        paper.abstract = row.text(6)
        paper.authors = row.jsonStringArray(7)
        paper.journal = row.text(8)
        paper.url = row.text(9)
        paper.published = row.text(10)
        paper.firstSeen = row.isoDate(11) ?? .distantPast
        paper.lastSeen = row.isoDate(12) ?? .distantPast
        paper.pubTypes = row.jsonStringArray(13)
        paper.sources = row.jsonStringArray(14)
        paper.score = row.double(15)
        paper.scoreBreakdown = row.jsonNumberDictionary(16)
        paper.priority = row.text(17) ?? "low"
        paper.appraisalJSON = row.text(18)   // engine JSON verbatim; typed via Paper.appraisal
        paper.appraisalAt = row.isoDate(19)
        paper.appraisalModel = row.text(20)
        paper.appraisalError = row.text(21)
        paper.topical = row.int64(22) != 0
        paper.journalTier = row.text(23)
        paper.archivedAt = row.isoDate(24)
        paper.zoteroKey = row.text(25)
        // Deliberately untouched: readAt, starred (native-only state).
    }

    /// Thin typed accessors over one result row of the legacy schema, which
    /// stores dates as ISO-8601 text and lists/dicts as JSON text.
    private struct Row {
        let stmt: OpaquePointer

        // Sendable, unlike ISO8601DateFormatter. Engine timestamps look like
        // 2026-07-25T15:30:00+00:00.
        static let iso = Date.ISO8601FormatStyle(includingFractionalSeconds: false)

        func text(_ index: Int32) -> String? {
            guard let c = sqlite3_column_text(stmt, index) else { return nil }
            let s = String(cString: c)
            return s.isEmpty ? nil : s
        }

        func int64(_ index: Int32) -> Int64 { sqlite3_column_int64(stmt, index) }
        func double(_ index: Int32) -> Double { sqlite3_column_double(stmt, index) }

        func isoDate(_ index: Int32) -> Date? {
            text(index).flatMap { try? Date($0, strategy: Self.iso) }
        }

        func json<T: Decodable>(_ index: Int32, as type: T.Type) -> T? {
            guard let s = text(index), let data = s.data(using: .utf8) else { return nil }
            return try? JSONDecoder().decode(type, from: data)
        }

        func jsonStringArray(_ index: Int32) -> [String] {
            json(index, as: [String].self) ?? []
        }

        func jsonNumberDictionary(_ index: Int32) -> [String: Double] {
            json(index, as: [String: Double].self) ?? [:]
        }
    }
}
