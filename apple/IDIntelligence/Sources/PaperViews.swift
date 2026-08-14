import IDIntelKit
import SwiftData
import SwiftUI

/// One shared archive path so the row button, context menu and detail
/// toolbar behave identically: load credentials from the engine's
/// settings.yaml, ensure the collection (cached key, one retry when stale),
/// create the item, stamp the paper.
@MainActor
enum ZoteroArchiver {
    static func archive(_ paper: Paper) async throws {
        let settings = try Zotero.Settings.load(settingsFile: AppPaths.settingsFile)
        let client = Zotero.Client(settings: settings)
        var item = Zotero.Item(paper: paper, tag: settings.tag)
        do {
            item.collections = [try await collectionKey(client)]
            paper.zoteroKey = try await client.createItem(item)
        } catch {
            // Stale cached collection key (deleted/renamed): refresh and retry once.
            UserDefaults.standard.removeObject(forKey: AppPaths.collectionKeyDefault)
            item.collections = [try await collectionKey(client)]
            paper.zoteroKey = try await client.createItem(item)
        }
        paper.archivedAt = .now
    }

    private static func collectionKey(_ client: Zotero.Client) async throws -> String {
        if let cached = UserDefaults.standard.string(forKey: AppPaths.collectionKeyDefault) {
            return cached
        }
        let key = try await client.ensureCollection()
        UserDefaults.standard.set(key, forKey: AppPaths.collectionKeyDefault)
        return key
    }
}

/// The send-to-Zotero mark: Zotero's unmistakable red "Z" (drawn natively —
/// no trademarked asset shipped), so the button reads instantly to a Zotero
/// user, unlike a generic SF Symbol.
struct ZoteroMark: View {
    var size: CGFloat = 13

    static let zoteroRed = Color(red: 0.80, green: 0.16, blue: 0.21)

    var body: some View {
        Text("Z")
            .font(.system(size: size, weight: .heavy, design: .serif))
            .foregroundStyle(Self.zoteroRed)
    }
}

/// Journal-tier badge with the same colour semantics as the HTML digest:
/// green = Top general / Agency, blue = Core ID, grey = Specialist,
/// amber = Unranked (the screen-this-venue cue).
struct TierBadge: View {
    let tier: String?

    var body: some View {
        Text(tier ?? "Unranked")
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.18), in: Capsule())
            .foregroundStyle(color)
    }

    private var color: Color {
        switch tier {
        case "Top general", "Agency / society": .green
        case "Core ID": .blue
        case "Specialist": .secondary
        default: .orange
        }
    }
}

struct PaperRow: View {
    let paper: Paper
    @State private var archiving = false
    @State private var archiveError: String?

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Circle()
                .fill(paper.readAt == nil ? Color.accentColor : .clear)
                .frame(width: 7, height: 7)
                .padding(.top, 5)
            VStack(alignment: .leading, spacing: 3) {
                Text(paper.title)
                    .font(.callout.weight(.medium))
                    .lineLimit(2)
                HStack(spacing: 6) {
                    TierBadge(tier: paper.journalTier)
                    Text(paper.journal ?? "—")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    if paper.isPreprint {
                        Text("preprint").font(.caption2).foregroundStyle(.orange)
                    }
                    if paper.isTrialRegistration {
                        Text("trial").font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 3) {
                Text(paper.score.formatted(.number.precision(.fractionLength(0))))
                    .font(.callout.monospacedDigit().weight(.semibold))
                if let stars = paper.stars {
                    Text(String(repeating: "★", count: stars))
                        .font(.caption2)
                        .foregroundStyle(.yellow)
                }
                if paper.starred {
                    Image(systemName: "star.fill")
                        .font(.caption2)
                        .foregroundStyle(.yellow)
                }
                // Per-record Zotero action, like the HTML digest's card
                // button — no need to open the paper first.
                if paper.zoteroKey != nil {
                    Image(systemName: "checkmark.seal.fill")
                        .font(.caption2)
                        .foregroundStyle(.green)
                        .help("In Zotero")
                } else if archiving {
                    ProgressView().controlSize(.mini)
                } else {
                    Button {
                        Task { await sendToZotero() }
                    } label: {
                        ZoteroMark()
                    }
                    .buttonStyle(.borderless)
                    .help("Send to the Zotero “ID Intelligence” collection")
                }
            }
        }
        .padding(.vertical, 2)
        // Read papers recede, like the HTML digest's archived cards — the
        // unread dot marks what's new, the dimming marks what's been seen.
        .opacity(paper.readAt == nil ? 1 : 0.55)
        .contextMenu {
            if paper.zoteroKey == nil {
                Button("Send to Zotero", systemImage: "plus.square.on.square") {
                    Task { await sendToZotero() }
                }
            }
            Button(paper.starred ? "Unstar" : "Star",
                   systemImage: paper.starred ? "star.slash" : "star") {
                paper.starred.toggle()
            }
            if paper.readAt != nil {
                Button("Mark Unread", systemImage: "circle.fill") {
                    paper.readAt = nil
                }
            }
        }
        .alert("Zotero", isPresented: .init(
            get: { archiveError != nil },
            set: { if !$0 { archiveError = nil } })
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(archiveError ?? "")
        }
    }

    private func sendToZotero() async {
        archiving = true
        defer { archiving = false }
        do {
            try await ZoteroArchiver.archive(paper)
        } catch {
            archiveError = "\(error)"
        }
    }
}

struct PaperDetailView: View {
    @Bindable var paper: Paper
    @State private var archiving = false
    @State private var zoteroError: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text(paper.title).font(.title2.weight(.semibold))

                HStack(spacing: 8) {
                    TierBadge(tier: paper.journalTier)
                    Text(paper.journal ?? "—").font(.subheadline)
                    if let published = paper.published {
                        Text(published).font(.subheadline).foregroundStyle(.secondary)
                    }
                    scoreChip
                }

                if !paper.authors.isEmpty {
                    Text(paper.authors.prefix(8).joined(separator: ", ")
                         + (paper.authors.count > 8 ? " et al." : ""))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if let appraisal = paper.appraisal {
                    AppraisalCard(appraisal: appraisal, model: paper.appraisalModel)
                }

                if let abstract = paper.abstract {
                    GroupBox("Abstract") {
                        Text(abstract)
                            .font(.body)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                }
            }
            .padding(20)
            .frame(maxWidth: 760, alignment: .leading)
        }
        .toolbar {
            ToolbarItemGroup {
                Button(paper.starred ? "Unstar" : "Star",
                       systemImage: paper.starred ? "star.fill" : "star") {
                    paper.starred.toggle()
                }
                zoteroButton
                if let url = linkURL {
                    Link(destination: url) { Label("Open", systemImage: "safari") }
                }
            }
        }
        .alert("Zotero", isPresented: .init(
            get: { zoteroError != nil },
            set: { if !$0 { zoteroError = nil } })
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(zoteroError ?? "")
        }
    }

    @ViewBuilder
    private var zoteroButton: some View {
        if paper.zoteroKey != nil {
            Label("In Zotero", systemImage: "checkmark.seal.fill")
                .foregroundStyle(.green)
        } else if archiving {
            ProgressView().controlSize(.small)
        } else {
            Button {
                Task { await archiveToZotero() }
            } label: {
                HStack(spacing: 3) {
                    ZoteroMark(size: 14)
                    Text("Zotero")
                }
            }
            .help("Send to the Zotero “ID Intelligence” collection")
        }
    }

    private func archiveToZotero() async {
        archiving = true
        defer { archiving = false }
        do {
            try await ZoteroArchiver.archive(paper)
        } catch {
            zoteroError = "\(error)"
        }
    }

    private var linkURL: URL? {
        if let url = paper.url, let u = URL(string: url) { return u }
        if let doi = paper.doi { return URL(string: "https://doi.org/\(doi)") }
        return nil
    }

    private var scoreChip: some View {
        Text("score \(paper.score.formatted(.number.precision(.fractionLength(1))))")
            .font(.caption.monospacedDigit())
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(.quaternary, in: Capsule())
            .help(breakdownText)
    }

    private var breakdownText: String {
        paper.scoreBreakdown
            .sorted { $0.value > $1.value }
            .map { "\($0.key): \($0.value.formatted(.number.precision(.fractionLength(0))))" }
            .joined(separator: "\n")
    }
}

/// The Claude appraisal, rendered like the HTML digest card's summary block.
struct AppraisalCard: View {
    let appraisal: Appraisal
    let model: String?

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(String(repeating: "★", count: appraisal.stars)
                         + String(repeating: "☆", count: 5 - appraisal.stars))
                        .foregroundStyle(.yellow)
                    Text("Read: \(appraisal.readFull)")
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(verdictColor.opacity(0.18), in: Capsule())
                        .foregroundStyle(verdictColor)
                    Spacer()
                    Text(appraisal.design)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Text(appraisal.bottomLine).font(.body.weight(.medium))
                Text(appraisal.whyItMatters).font(.callout)

                bulletList("Strengths", appraisal.strengths, tint: .green)
                bulletList("Weaknesses", appraisal.weaknesses, tint: .red)

                if !appraisal.topics.isEmpty {
                    HStack(spacing: 4) {
                        ForEach(appraisal.topics, id: \.self) { topic in
                            Text(topic)
                                .font(.caption2)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(.quaternary, in: Capsule())
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: {
            Label("AI appraisal\(model.map { " · \($0)" } ?? "")", systemImage: "sparkles")
        }
    }

    private var verdictColor: Color {
        switch appraisal.readFull {
        case "yes": .green
        case "skim": .orange
        default: .secondary
        }
    }

    private func bulletList(_ title: String, _ items: [String], tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(tint)
            ForEach(items, id: \.self) { item in
                HStack(alignment: .top, spacing: 5) {
                    Text("•").foregroundStyle(tint)
                    Text(item).font(.callout)
                }
            }
        }
    }
}
