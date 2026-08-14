import Foundation
import SwiftData
import XCTest
@testable import IDIntelKit

/// Offline tests mirroring the Python engine's byline/item behaviour — the
/// same cases used to validate `util.split_byline` and `zotero.py`.
final class ZoteroTests: XCTestCase {

    // ------------------------------------------------------ byline splitting

    func testJoinedBylineSplits() {
        XCTAssertEqual(
            Zotero.splitByline(["Jan Styczynski, Gloria Tridello, Nina Knelange, Per Ljungman"]),
            ["Jan Styczynski", "Gloria Tridello", "Nina Knelange", "Per Ljungman"])
    }

    func testPubMedStyleUntouched() {
        XCTAssertEqual(
            Zotero.splitByline(["Grzybek M", "Sironen T", "Henttonen H"]),
            ["Grzybek M", "Sironen T", "Henttonen H"])
    }

    func testLoneLastFirstKeptIntact() {
        XCTAssertEqual(Zotero.splitByline(["Styczynski, Jan"]), ["Styczynski, Jan"])
    }

    func testAndSeparatorSplits() {
        XCTAssertEqual(
            Zotero.splitByline(["Smith J, Jones K and Lee R"]),
            ["Smith J", "Jones K", "Lee R"])
    }

    func testEmptyEntriesDropped() {
        XCTAssertEqual(Zotero.splitByline(["", "  "]), [])
    }

    func testOverlongNameClampedToSyncLimit() {
        let huge = String(repeating: "X", count: 400)
        XCTAssertEqual(Zotero.splitByline([huge]).map(\.count), [255])
    }

    // ------------------------------------------------------- item building

    @MainActor
    func testItemMirrorsEnginePayload() {
        let paper = Paper()
        paper.title = "Cefiderocol in clinical practice"
        paper.journal = "J Infect"
        paper.published = "2026-08-01"
        paper.doi = "10.1000/xyz"
        paper.pmid = "12345678"
        paper.nct = "NCT01234567"
        paper.authors = ["Smith J, Jones K, Lee R"]
        paper.abstract = "An abstract."
        paper.score = 42.35
        paper.priority = "high"
        paper.sources = ["PubMed: Core ID journals"]
        paper.appraisal = Appraisal(
            bottomLine: "x", whyItMatters: "y", strengths: [], weaknesses: [],
            readFull: "yes", readFullReason: nil, stars: 4,
            topics: ["cefiderocol", "CRAB", "", "PK/PD"], design: "review")

        let item = Zotero.Item(paper: paper, tag: "idintel")

        XCTAssertEqual(item.itemType, "journalArticle")
        XCTAssertEqual(item.creators.map(\.name), ["Smith J", "Jones K", "Lee R"])
        XCTAssertEqual(item.publicationTitle, "J Infect")
        XCTAssertEqual(item.DOI, "10.1000/xyz")
        XCTAssertTrue(item.extra.contains("NCT: NCT01234567"))
        XCTAssertTrue(item.extra.contains("PMID: 12345678"))
        XCTAssertTrue(item.extra.contains("idintel score: 42.4 (high)"))
        XCTAssertTrue(item.extra.contains("Source: PubMed: Core ID journals"))
        // idintel tag first, then non-empty topics (max 5).
        XCTAssertEqual(item.tags.map(\.tag), ["idintel", "cefiderocol", "CRAB", "PK/PD"])
    }

    @MainActor
    func testItemWithoutAuthorsGetsPlaceholderAndDOIURL() {
        let paper = Paper()
        paper.title = "Untitled trial"
        paper.doi = "10.1000/abc"
        let item = Zotero.Item(paper: paper, tag: "idintel")
        XCTAssertEqual(item.creators.map(\.name), ["[No author listed]"])
        XCTAssertEqual(item.url, "https://doi.org/10.1000/abc")
    }

    // ------------------------------------------------------ settings parsing

    func testSettingsLoadFromYAML() throws {
        let yaml = """
        pubmed:
          api_key: "unrelated"
        zotero:
          api_key: "zkey"
          library_id: "5339168"
          library_type: user
          collection: "ID Intelligence"
          tag: idintel
        """
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent("settings-\(UUID().uuidString).yaml")
        try yaml.write(to: file, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: file) }

        let settings = try Zotero.Settings.load(settingsFile: file)
        XCTAssertEqual(settings.apiKey, "zkey")
        XCTAssertEqual(settings.libraryID, "5339168")
        XCTAssertEqual(settings.collection, "ID Intelligence")
        XCTAssertEqual(settings.tag, "idintel")
    }

    // ------------------------------------------------------- URL building

    func testQueryStringSurvivesURLBuilding() {
        // Regression: URL.appending(path:) percent-encoded the "?" so Zotero
        // returned its HTML 404 page for every request.
        let client = Zotero.Client(settings: .init(apiKey: "k", libraryID: "5339168"))
        XCTAssertEqual(client.url(for: "collections?limit=100").absoluteString,
                       "https://api.zotero.org/users/5339168/collections?limit=100")
        XCTAssertEqual(client.url(for: "items").absoluteString,
                       "https://api.zotero.org/users/5339168/items")
    }

    /// Live round-trip against the real account — network + real credentials,
    /// so opt-in only: `ZOTERO_LIVE=1 swift test`. Read-only (collection
    /// lookup); creates nothing.
    func testLiveCollectionLookup() async throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["ZOTERO_LIVE"] == "1",
                          "set ZOTERO_LIVE=1 to run against the real API")
        let settingsFile = URL(fileURLWithPath: LegacyStoreImporterTests.databasePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("config/settings.yaml")
        let settings = try Zotero.Settings.load(settingsFile: settingsFile)
        let key = try await Zotero.Client(settings: settings).ensureCollection()
        XCTAssertFalse(key.isEmpty, "collection key resolved")
    }

    func testSettingsRejectMissingKey() throws {
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent("settings-\(UUID().uuidString).yaml")
        try "zotero:\n  api_key: \"\"\n  library_id: \"\"\n"
            .write(to: file, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: file) }
        XCTAssertThrowsError(try Zotero.Settings.load(settingsFile: file))
    }
}
