import Foundation
import SwiftData

/// One appraisal produced by the Claude CLI, mirroring the JSON schema in
/// the Python engine's `summarize.py`. Stored as a Codable value on `Paper`
/// rather than a separate model — appraisals are immutable once produced and
/// always fetched with their paper.
public struct Appraisal: Codable, Hashable, Sendable {
    public var bottomLine: String
    public var whyItMatters: String
    public var strengths: [String]
    public var weaknesses: [String]
    /// "yes" | "skim" | "no"
    public var readFull: String
    public var readFullReason: String?
    /// 1–5
    public var stars: Int
    public var topics: [String]
    public var design: String

    public init(bottomLine: String, whyItMatters: String, strengths: [String],
                weaknesses: [String], readFull: String, readFullReason: String?,
                stars: Int, topics: [String], design: String) {
        self.bottomLine = bottomLine
        self.whyItMatters = whyItMatters
        self.strengths = strengths
        self.weaknesses = weaknesses
        self.readFull = readFull
        self.readFullReason = readFullReason
        self.stars = stars
        self.topics = topics
        self.design = design
    }

    /// Keys as emitted by the Python engine (snake_case JSON in SQLite).
    enum CodingKeys: String, CodingKey {
        case bottomLine = "bottom_line"
        case whyItMatters = "why_it_matters"
        case strengths, weaknesses
        case readFull = "read_full"
        case readFullReason = "read_full_reason"
        case stars, topics, design
    }
}

/// A collected literature record — one row of the Python engine's `records`
/// table. Every stored property has a default and nothing is marked unique:
/// both are hard requirements for CloudKit mirroring (see plan §2/D2), so the
/// schema is CloudKit-shaped from day one.
@Model
public final class Paper {
    /// `records.id` in the Python SQLite database. Used by the importer to
    /// upsert incrementally; 0 means "created natively, no legacy row".
    public var legacyID: Int64 = 0

    // ---------------------------------------------------------- identity
    public var kind: String = "article"           // article | preprint | trial
    public var doi: String?
    public var pmid: String?
    public var nct: String?
    public var title: String = ""
    public var abstract: String?
    public var authors: [String] = []
    public var journal: String?
    public var journalTier: String?               // Top general / Core ID / … / Unranked
    public var url: String?
    /// ISO `YYYY-MM-DD` as stored by the engine; lexicographic order == date order.
    public var published: String?
    public var pubTypes: [String] = []
    public var sources: [String] = []
    public var firstSeen: Date = Date.distantPast
    public var lastSeen: Date = Date.distantPast

    // ---------------------------------------------------------- scoring
    public var score: Double = 0
    public var scoreBreakdown: [String: Double] = [:]
    public var priority: String = "low"           // critical | high | medium | low
    public var topical: Bool = false

    // ---------------------------------------------------------- appraisal
    /// The engine's appraisal JSON verbatim (snake_case, as produced by
    /// `summarize.py`). Stored as a plain string: SwiftData's composite
    /// storage for Codable properties traps when re-decoding types with
    /// custom CodingKeys, and a single String attribute is also the simplest
    /// thing CloudKit can mirror. Use `appraisal` for typed access.
    public var appraisalJSON: String?
    public var appraisalModel: String?
    public var appraisalAt: Date?
    public var appraisalError: String?

    /// Typed view over `appraisalJSON`; nil when absent or undecodable.
    public var appraisal: Appraisal? {
        get {
            guard let data = appraisalJSON?.data(using: .utf8) else { return nil }
            return try? JSONDecoder().decode(Appraisal.self, from: data)
        }
        set {
            guard let newValue,
                  let data = try? JSONEncoder().encode(newValue) else {
                appraisalJSON = nil
                return
            }
            appraisalJSON = String(decoding: data, as: UTF8.self)
        }
    }

    // ---------------------------------------------------------- filing
    public var zoteroKey: String?
    public var archivedAt: Date?

    // ------------------------------------------------- native-only state
    // The HTML reports never had these; they are the app's value-add and are
    // preserved across re-imports.
    public var readAt: Date?
    public var starred: Bool = false

    public init() {}

    /// Star rating shown on cards: the appraisal's stars, if appraised.
    public var stars: Int? { appraisal?.stars }

    public var isPreprint: Bool { kind == "preprint" }
    public var isTrialRegistration: Bool { kind == "trial" }
}
