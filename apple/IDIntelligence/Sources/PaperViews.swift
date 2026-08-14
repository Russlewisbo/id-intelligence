import IDIntelKit
import SwiftData
import SwiftUI

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
                if paper.zoteroKey != nil {
                    Image(systemName: "checkmark.seal.fill")
                        .font(.caption2)
                        .foregroundStyle(.green)
                        .help("In Zotero")
                }
            }
        }
        .padding(.vertical, 2)
        // Read papers recede, like the HTML digest's archived cards — the
        // unread dot marks what's new, the dimming marks what's been seen.
        .opacity(paper.readAt == nil ? 1 : 0.55)
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
            Button("Zotero", systemImage: "plus.square.on.square") {
                Task { await archiveToZotero() }
            }
            .help("File this paper into the ID Intelligence collection")
        }
    }

    private func archiveToZotero() async {
        archiving = true
        defer { archiving = false }
        do {
            let settings = try Zotero.Settings.load(settingsFile: AppPaths.settingsFile)
            let client = Zotero.Client(settings: settings)

            // Collection key is cached; a stale key (collection deleted or
            // renamed) fails the create, so clear the cache and retry once.
            var item = Zotero.Item(paper: paper, tag: settings.tag)
            do {
                item.collections = [try await collectionKey(client)]
                paper.zoteroKey = try await client.createItem(item)
            } catch {
                UserDefaults.standard.removeObject(forKey: AppPaths.collectionKeyDefault)
                item.collections = [try await collectionKey(client)]
                paper.zoteroKey = try await client.createItem(item)
            }
            paper.archivedAt = .now
        } catch {
            zoteroError = "\(error)"
        }
    }

    private func collectionKey(_ client: Zotero.Client) async throws -> String {
        if let cached = UserDefaults.standard.string(forKey: AppPaths.collectionKeyDefault) {
            return cached
        }
        let key = try await client.ensureCollection()
        UserDefaults.standard.set(key, forKey: AppPaths.collectionKeyDefault)
        return key
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
