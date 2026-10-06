import Foundation
import Yams

/// Native port of the engine's `zotero.py`: files a paper into the user's
/// Zotero library over the Web API. Items appear in desktop Zotero on its
/// next sync. Unlike the Python flow there is no localhost companion server —
/// the app talks to api.zotero.org directly, and the API key never leaves it.
public enum Zotero {

    // ------------------------------------------------------------ settings

    /// The `zotero:` block of the engine's git-ignored `config/settings.yaml`
    /// — the shared tuning surface (plan D4), so both worlds use one config.
    public struct Settings: Sendable, Equatable {
        public var apiKey: String
        public var libraryID: String
        public var libraryType: String   // "user" | "group"
        public var collection: String
        public var tag: String

        public init(apiKey: String, libraryID: String, libraryType: String = "user",
                    collection: String = "ID Intelligence", tag: String = "idintel") {
            self.apiKey = apiKey
            self.libraryID = libraryID
            self.libraryType = libraryType
            self.collection = collection
            self.tag = tag
        }

        public static func load(settingsFile: URL) throws -> Settings {
            let text = try String(contentsOf: settingsFile, encoding: .utf8)
            guard let root = try Yams.load(yaml: text) as? [String: Any],
                  let z = root["zotero"] as? [String: Any] else {
                throw ClientError("no `zotero:` section in \(settingsFile.path)")
            }
            let apiKey = z["api_key"] as? String ?? ""
            let libraryID = (z["library_id"] as? String)
                ?? (z["library_id"] as? Int).map(String.init) ?? ""
            guard !apiKey.isEmpty, !libraryID.isEmpty else {
                throw ClientError("zotero.api_key / library_id not configured in settings.yaml")
            }
            return Settings(
                apiKey: apiKey,
                libraryID: libraryID,
                libraryType: z["library_type"] as? String ?? "user",
                collection: z["collection"] as? String ?? "ID Intelligence",
                tag: z["tag"] as? String ?? "idintel")
        }
    }

    public struct ClientError: Error, CustomStringConvertible {
        public let description: String
        init(_ message: String) { description = message }
    }

    // ------------------------------------------------------------- payload

    /// A `journalArticle` item as `zotero.py` builds it. Everything is filed
    /// as journalArticle — a record's true nature (preprint, trial) stays
    /// visible in the journal name and abstract. One deviation: PMID goes
    /// into `extra` (Zotero convention) rather than a nonstandard field.
    public struct Item: Encodable, Sendable {
        public struct Creator: Encodable, Sendable, Equatable {
            public var creatorType = "author"
            public var name: String
            public init(name: String) { self.name = name }
        }
        public struct Tag: Encodable, Sendable, Equatable {
            public var tag: String
            public init(tag: String) { self.tag = tag }
        }

        public var itemType = "journalArticle"
        public var title: String
        public var creators: [Creator]
        public var publicationTitle: String
        public var date: String
        public var DOI: String
        public var url: String
        public var abstractNote: String
        public var extra: String
        public var tags: [Tag]
        public var accessDate: String
        public var collections: [String] = []

        public init(paper: Paper, tag: String) {
            let authors = splitByline(paper.authors).map(Creator.init(name:))
            creators = authors.isEmpty ? [Creator(name: "[No author listed]")] : authors

            var extraLines: [String] = []
            if let nct = paper.nct { extraLines.append("NCT: \(nct)") }
            if let pmid = paper.pmid { extraLines.append("PMID: \(pmid)") }
            extraLines.append("idintel score: \((paper.score * 10).rounded() / 10) (\(paper.priority))")
            if !paper.sources.isEmpty {
                extraLines.append("Source: " + paper.sources.joined(separator: "; "))
            }

            title = paper.title.isEmpty ? "[untitled]" : paper.title
            publicationTitle = paper.journal ?? ""
            date = paper.published ?? ""
            DOI = paper.doi ?? ""
            url = paper.url ?? paper.doi.map { "https://doi.org/\($0)" } ?? ""
            abstractNote = paper.abstract ?? ""
            extra = extraLines.joined(separator: "\n")
            tags = [Tag(tag: tag)] + (paper.appraisal?.topics.prefix(5) ?? [])
                .filter { !$0.isEmpty }
                .map(Tag.init(tag:))
            accessDate = Date.now.formatted(.iso8601.year().month().day())
        }
    }

    /// Zotero refuses to sync creator names above 255 characters.
    static let creatorNameLimit = 255

    /// Port of the engine's `util.split_byline` + `zotero._expand_authors`:
    /// one person per element. A string splitting into 3+ parts is a joined
    /// byline and is broken apart; a lone "Last, First" (2 parts) stays
    /// intact. Every name is clamped to Zotero's sync limit.
    public static func splitByline(_ names: [String]) -> [String] {
        var out: [String] = []
        for name in names {
            let trimmed = name.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty else { continue }
            let parts = trimmed
                .split(separator: /\s*(?:,|;| and | & )\s*/)
                .map(String.init)
                .filter { !$0.isEmpty }
            let pieces = parts.count >= 3 ? parts : [trimmed]
            out.append(contentsOf: pieces.map { String($0.prefix(creatorNameLimit)) })
        }
        return out
    }

    // -------------------------------------------------------------- client

    public final class Client: Sendable {
        let settings: Settings
        let session: URLSession

        public init(settings: Settings, session: URLSession = .shared) {
            self.settings = settings
            self.session = session
        }

        var base: URL {
            URL(string: "https://api.zotero.org/\(settings.libraryType)s/\(settings.libraryID)")!
        }

        /// Builds the request URL. NOTE: must NOT use `URL.appending(path:)`
        /// with a query string — it percent-encodes the `?` into a literal
        /// path character, which Zotero answers with an HTML 404 page.
        func url(for path: String) -> URL {
            URL(string: "\(base.absoluteString)/\(path)")!
        }

        /// Finds the target collection (top-level, by name), creating it if
        /// missing — same behaviour as `zotero.py`. Returns its key.
        public func ensureCollection() async throws -> String {
            let list = try await request("GET", "collections?limit=100")
            if let collections = list as? [[String: Any]] {
                for entry in collections {
                    guard let data = entry["data"] as? [String: Any] else { continue }
                    let parent = data["parentCollection"]
                    let isTopLevel = parent == nil || parent is Bool && (parent as? Bool) == false
                    if data["name"] as? String == settings.collection, isTopLevel,
                       let key = data["key"] as? String {
                        return key
                    }
                }
            }
            let created = try await request(
                "POST", "collections", body: [["name": settings.collection]])
            return try successKey(from: created, what: "collection")
        }

        /// Creates the item and returns its Zotero key.
        public func createItem(_ item: Item) async throws -> String {
            let data = try JSONEncoder().encode([item])
            let response = try await request("POST", "items", rawBody: data)
            return try successKey(from: response, what: "item")
        }

        // ---------------------------------------------------------- plumbing

        private func successKey(from response: Any, what: String) throws -> String {
            guard let dict = response as? [String: Any] else {
                throw ClientError("unexpected Zotero response for \(what)")
            }
            if let successful = dict["successful"] as? [String: Any],
               let first = successful["0"] as? [String: Any],
               let key = first["key"] as? String {
                return key
            }
            if let failed = dict["failed"] as? [String: Any],
               let first = failed["0"] as? [String: Any],
               let message = first["message"] as? String {
                throw ClientError("Zotero rejected the \(what): \(message)")
            }
            throw ClientError("unexpected Zotero response for \(what)")
        }

        private func request(_ method: String, _ path: String,
                             body: Any? = nil, rawBody: Data? = nil) async throws -> Any {
            var req = URLRequest(url: url(for: path))
            req.httpMethod = method
            req.setValue(settings.apiKey, forHTTPHeaderField: "Zotero-API-Key")
            req.setValue("3", forHTTPHeaderField: "Zotero-API-Version")
            if let body {
                req.httpBody = try JSONSerialization.data(withJSONObject: body)
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            } else if let rawBody {
                req.httpBody = rawBody
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            }
            let (data, response) = try await session.data(for: req)
            guard let http = response as? HTTPURLResponse else {
                throw ClientError("no HTTP response from Zotero")
            }
            guard (200..<300).contains(http.statusCode) else {
                let detail = String(decoding: data.prefix(300), as: UTF8.self)
                throw ClientError("Zotero HTTP \(http.statusCode): \(detail)")
            }
            return try JSONSerialization.jsonObject(with: data)
        }
    }
}
