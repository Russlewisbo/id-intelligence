import Foundation
import SwiftData

/// A journal the reader has unchecked in the journal picker: papers from it
/// are hidden from the Digest scope (All Papers always shows everything).
/// Presence of a row means excluded; included is the default for any journal
/// never seen before, so newly collected venues surface until vetoed.
/// Native-only preference — the Python engine's HTML reports keep their own
/// YAML blocklist until the engine port makes this the single control.
@Model
public final class ExcludedJournal {
    /// Exact journal name as stored on `Paper.journal`.
    public var name: String = ""

    public init(name: String = "") {
        self.name = name
    }
}
