# Native Swift ID Intelligence — Migration Plan

**Goal:** a native macOS + iPhone app for the ID Intelligence system — same
pipeline (collect → dedupe → score → appraise → digest), but with a real app
experience: native digest UI, sync to the phone, notifications, and one-tap
Zotero filing.

**Strategy in one sentence:** keep the proven Python engine running while a
native SwiftUI front end is built on top of a synced data store, then port the
engine to Swift piece by piece — never a big-bang rewrite.

---

## 1. What exists today (review)

| Component | Size | Portability to Swift |
|---|---|---|
| Collectors — RSS, PubMed, bioRxiv/medRxiv, ClinicalTrials.gov | ~610 lines | Straightforward: URLSession + XML/JSON parsing (FeedKit for RSS) |
| Dedupe — DOI → PMID → NCT → exact → fuzzy title, additive merge | 178 | Direct port; fuzzy match needs a Swift similarity function |
| Scoring — YAML regex rules, journal tiers, topical gate | 180 + 339 YAML | Direct port; **keep the same YAML files** (Yams) so tuning stays shared |
| Appraisal — `claude -p` subprocess, JSON schema, retry semantics | 306 | Mac: `Process` → claude CLI (works). **iPhone: impossible** — no subprocesses. This is the key architectural constraint |
| Reports — Jinja2 → static HTML | 279 + 450 templates | Replaced by native SwiftUI views (the biggest UX win) |
| Zotero — Web API client | 179 | Direct port over URLSession |
| Serve — local HTTP for archive buttons | 163 | Disappears — native UI calls Zotero directly |
| Storage — SQLite, 3 tables, 30-column `records` | 193 | SwiftData models + one-time importer from `data/idintel.db` |
| Scheduling — launchd agents | — | Mac: menu-bar app with internal scheduler (launchd retired at the end) |

~2,900 lines of Python total. Nothing exotic; the engine ports cleanly. The
two genuinely hard problems are **where appraisal runs for the phone** and
**sync**.

---

## 2. Architecture (recommended): Mac as hub, iPhone as satellite

```
┌─────────────── Mac app ───────────────┐        ┌──── iPhone app ────┐
│ collect → dedupe → score → appraise   │        │  digest reader      │
│ (appraise = Process → claude CLI,     │ CloudKit│  triage: star/read  │
│  keeps the subscription, no API key)  │ ─sync─▶ │  + Zotero button   │
│ SwiftData store (CloudKit-mirrored)   │ ◀─sync─ │  read/star state    │
└───────────────────────────────────────┘        └────────────────────┘
```

**Why:** the whole system's premise is *"uses your Claude subscription via the
`claude` CLI — no API key, no metered billing."* A Mac app can spawn that CLI;
an iPhone app cannot. Running the pipeline on the Mac and syncing results via
CloudKit preserves the billing model, keeps one authoritative database, and
gives the phone exactly what a phone is for: reading the morning digest over
coffee, starring, and filing to Zotero. iOS background-collection would also
be unreliable (BGAppRefreshTask gives no guarantees) — the Mac hub avoids that
entirely.

**Alternative (rejected):** full pipeline on both platforms. Requires an
`ANTHROPIC_API_KEY` on iOS (metered billing), duplicates collection, and
fights iOS background-execution limits.

### Key decisions — **settled 2026-08-13**

| # | Decision | Chosen |
|---|---|---|
| D1 | Where does appraisal run? | ✅ Mac only (claude CLI → subscription billing, no API key) |
| D2 | Storage & sync | SwiftData + CloudKit mirroring (default accepted) |
| D3 | Distribution | ✅ Personal — run on own devices; no App Store |
| D4 | Tuning surface | Keep `scoring.yaml` / `feeds.yaml` / `settings.yaml` formats (default accepted) |
| D5 | Python engine end-state | ✅ **Full Swift rewrite** — Python retired once Swift reaches parity |

Requirements: Apple Developer Program membership (US$99/yr) for CloudKit and
device installs; Xcode (installed — needs `sudo xcode-select -s
/Applications/Xcode.app`).

---

## 3. Phases

### Phase 1 — Shared core package `IDIntelKit` (foundation)
Swift Package used by both apps.
- SwiftData models mirroring today's schema: `Record` (ids, title, abstract,
  authors, journal/tier, score + breakdown, priority, topical), `Appraisal`
  (bottom_line, why_it_matters, strengths, weaknesses, read_full, stars,
  topics, design), `FeedState`, `Run` — plus new `readAt` / `starred` fields
  the HTML reports never had.
- CloudKit mirroring enabled from day one (schema constraints: optionals,
  no unique constraints — design for this now, painful to retrofit).
- **SQLite importer**: one-time + incremental import from `data/idintel.db`
  so the Python engine keeps feeding the app during the whole migration.
- Zotero client port (create item, collection cache, sent-state).

*Exit criteria:* existing 3,800+ records visible in a SwiftData store, synced
to a second device via CloudKit.

### Phase 2 — macOS reader app
The digest, native. Python still runs the pipeline on its launchd schedule;
the app imports new records after each run (file-watch on the DB).
- Sidebar: Today / by priority / starred / reviews / search.
- Card list: tier badge, score breakdown popover, stars, preprint flag —
  everything `macros.html.j2` renders today, but live.
- Detail: abstract + appraisal, ⌘Z file-to-Zotero, open DOI/PubMed.
- Read/unread + star state (persisted, synced — the HTML reports could never
  do this).

*Exit criteria:* daily triage happens in the app instead of `latest-daily.html`;
the `serve` launchd agent can be turned off (Zotero goes direct).

### Phase 3 — iPhone app
Same SwiftUI views recomposed for iOS (one multiplatform target).
- CloudKit brings the data; no collection on the phone.
- Local notification when a new day's digest arrives.
- Swipe actions: star, mark read, send to Zotero.
- Widget: "N high-priority papers this morning".

*Exit criteria:* morning digest readable on the phone; state syncs both ways.

### Phase 4 — Port the engine into the Mac app
One collector at a time, verified against the Python output on the same day
(record counts, dedupe keys, scores must match before cut-over):
1. ClinicalTrials.gov (plain JSON — easiest)
2. bioRxiv/medRxiv (JSON)
3. PubMed E-utilities (XML; esearch/efetch, history server, rate limits)
4. RSS (FeedKit; etag/last-modified conditional fetches; byline splitting)
5. Dedupe + scoring (Yams reads the *same* `scoring.yaml`; regex semantics
   verified rule-by-rule; `rescore` parity check across all records)

*Exit criteria:* Swift and Python runs produce identical scored databases on
the same inputs for several consecutive days.

### Phase 5 — Appraisal + scheduling on the Mac app
- `Process` → `claude -p` with the same flags/prompt as `summarize.py`, same
  retryable-vs-content error split, same SQLite-style caching (never appraise
  a record twice).
- Menu-bar presence: next-run countdown, run-now, doctor-style health view.
- Internal scheduler (06:30 / Fri 07:15 / monthly) replaces the launchd
  agents; Persistent-style catch-up on wake.

*Exit criteria:* Python engine fully idle for a week with no regressions →
retire it (keep the repo; the YAML configs remain the shared tuning surface).

### Phase 6 — Native-only wins (optional, post-parity)
- HTML export for sharing a digest (port of today's templates).
- App Intents / Shortcuts ("appraise this DOI").
- On-device Foundation Models for instant pre-triage summaries while full
  Claude appraisals are pending.
- Weekly/monthly views (top-N, thematic groupings from `themes:`).

---

## 4. Risks & honest caveats

- **App Store is off the table** while appraisal shells out to the claude CLI
  (sandbox). Personal/Developer ID distribution is the plan (D3).
- **SwiftData + CloudKit** is the fastest path but has real constraints (all
  relationships optional, no unique constraints, eventual consistency, no
  server-side dedupe). The dedupe logic must therefore stay on the Mac hub —
  the phone never writes new records, only read/star state. If SwiftData
  chafes, GRDB is the escape hatch at the cost of hand-rolled sync.
- **CLI fragility travels with us**: the appraisal call inherits the same
  flag/model-alias drift we already fixed twice (`--json-schema`, retired
  `sonnet` alias). The Swift port should pin full model IDs and treat CLI
  errors as retryable exactly like `summarize.py` does today.
- **iPhone is read-mostly by design.** If you later want collection or
  appraisal without the Mac being awake, that's a server or API-key decision,
  not an iOS one.
- **Fuzzy-title dedupe parity** needs care: Python's `difflib` ratio must be
  reimplemented and validated against the same record pairs, or near-duplicate
  merging will silently diverge.

## 5. Suggested order of work

Phases 1–2 first (visible value: native Mac triage over the existing engine),
then 3 (phone). 4–5 are the long tail and can proceed at leisure — the system
stays fully functional throughout because Python keeps running until parity.

**First concrete milestone:** Xcode multiplatform project + `IDIntelKit` +
SQLite importer showing today's real digest in a native macOS list, filed to
Zotero with one click.
