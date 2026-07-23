# ID Intelligence System

Daily infectious-disease literature surveillance: collect → deduplicate →
score → AI appraisal → HTML digest. Runs locally, stores everything in SQLite,
and uses your existing Claude subscription through the `claude` CLI — no API
key and no metered billing.

```bash
./idintel.sh doctor          # check every source + Claude connectivity
./idintel.sh daily           # the full morning run
```

---

## How it works

| Stage | What happens |
|---|---|
| **1. Collect** | RSS (journals, FDA, EMA, CDC, WHO), PubMed E-utilities, bioRxiv/medRxiv, ClinicalTrials.gov |
| **2. Deduplicate** | DOI → PMID → NCT → exact title → fuzzy title. Merges additively, never loses an abstract |
| **3. Score** | Config-driven regex rules + journal tiers, with a stored breakdown of *why* |
| **4. Appraise** | `claude -p` with a JSON schema: bottom line, why it matters, strengths, weaknesses, read-it-or-not, ★1–5 |
| **5. Report** | Self-contained HTML — daily digest, Friday top 10, monthly thematic review |

Coverage is deliberately split. **PubMed queries** (`config/settings.yaml`) do
the heavy lifting for journal coverage: they return the structured abstract,
MeSH publication types and the DOI, which the scorer needs. **RSS feeds**
(`config/feeds.yaml`) exist for ahead-of-print articles that PubMed has not yet
indexed, and for agency output that never enters PubMed at all.

---

## Setup

From a fresh clone, create the virtualenv, install deps, and make your local
settings from the template (the real `config/settings.yaml` is git-ignored
because it holds your NCBI API key):

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/settings.example.yaml config/settings.yaml
# then put your NCBI key in the pubmed.api_key field
```

On the original machine this is already done: the `.venv` exists and
`config/settings.yaml` holds your key.

**1. Authenticate the CLI for headless use** (required for step 4).

The appraisal step shells out to `claude -p`. A detached subprocess cannot use
the auth that a running Claude Code session holds in memory, so it needs a
**long-lived token on disk**, generated once with your subscription:

```bash
claude setup-token
```

Then confirm it works:

```bash
cd "/home/ussellr/Desktop/ID app" && ./idintel.sh doctor
```

The Claude CLI section must report `✓`. Until it does, daily runs still
collect, dedupe, score and build the digest — they just leave the appraisals
blank. Records that fail appraisal for an auth or network reason are **left
pending, not discarded**, so once the token is in place a normal run fills them
in. (If you ever need to force a retry of records that failed for other reasons:
`./idintel.sh retry`.)

If `setup-token` is unavailable in your setup, the alternative is an API key
(metered billing): put `ANTHROPIC_API_KEY` in the environment the timer runs in.
That trades your "subscription, no key" preference for reliability.

**2. Install the timers** (06:30 daily, 07:15 Friday, 07:30 on the 1st):

```bash
"/home/ussellr/Desktop/ID app/systemd/install.sh"
```

Timers use `Persistent=true`, so a run missed while the machine was off fires
at the next boot. To let them run when booted but logged out:
`sudo loginctl enable-linger $USER`.

---

## Commands

```bash
./idintel.sh daily              # collect + appraise + digest
./idintel.sh daily --no-summary # skip the AI step (fast, free)
./idintel.sh weekly --top 10    # Friday must-read
./idintel.sh monthly            # thematic review
./idintel.sh collect --only rss pubmed
./idintel.sh top --days 7 --limit 20
./idintel.sh rescore            # re-apply scoring rules to everything
./idintel.sh show 1234          # dump one record as JSON
./idintel.sh doctor -v          # per-source health
```

Reports land in `out/`, with `out/latest-daily.html` always pointing at the
newest one.

---

## Tuning

Everything lives in `config/`, so retuning never means touching Python.

**`scoring.yaml`** — the weights. A rule fires at most once and adds its weight:

```yaml
- id: hsct_infection
  label: HSCT infection
  weight: 12
  all_of:                       # every group must match (AND of ORs)
    - ["\\bHSCT\\b", "stem[- ]cell transplant", "allogeneic transplant"]
    - ["infect", "neutropeni", "\\bCMV\\b", "prophylax"]
```

`any_of` fires on any match, `all_of` requires a hit in every group, `none_of`
vetoes. Priority bands (`critical` ≥ 40, `high` ≥ 25, `medium` ≥ 12) decide what
gets appraised and how the digest is grouped. After editing, run `rescore`.

**`topical: true`** marks a rule as establishing ID subject matter (Aspergillus,
ESBL, HSCT infection…). Methodology rules (RCT, meta-analysis) and journal tiers
are *not* topical. The daily digest shows a record only if a topical rule fired
(or it scores ≥ `report.daily_keep_score`), which is what stops a randomised
trial about wine and driving — real points for being an RCT, zero ID content —
from appearing. Set `report.daily_require_topical: false` to see everything.

**Journal tiers** double as a quality signal. Every card shows a coloured badge:
*Top general* / *Agency* (green), *Core ID* (blue), *Specialist* (grey), or an
amber **Unranked** for any journal not in `journal_tiers` — your cue to screen
out unfamiliar or predatory venues. To promote a journal, add its name to the
appropriate tier's `match:` list.

The `themes:` block at the bottom uses the same syntax and drives the monthly
review sections.

**`settings.yaml`** — PubMed/trial queries, lookback windows, and the appraisal
budget:

```yaml
summary:
  min_score: 22      # only records scoring this high are sent to the model
  max_per_run: 35    # hard ceiling on model calls per run
  workers: 3         # concurrent claude processes
```

**`feeds.yaml`** — RSS sources. Every URL was verified live on 2026-07-22.

---

## Known limitations

**ASM and MDPI feeds return HTTP 403.** `journals.asm.org` (AAC, JCM, mBio,
Microbiology Spectrum, CMR) and `mdpi.com` (J Fungi, Antibiotics) sit behind
bot protection that rejects any non-browser client regardless of user-agent.
They are listed under `groups.blocked` in `feeds.yaml`, disabled. **No coverage
is lost** — every one of those journals is captured by the PubMed queries; only
ahead-of-print latency is affected. Working around the bot protection would
mean impersonating a browser, which this project does not do.

**BioMedCentral** (BMC Infect Dis, ARIC) now serves HTML instead of RSS at the
documented feed paths. Also covered by PubMed.

**WHO Disease Outbreak News** no longer advertises a feed URL; `who.int` returns
404 for every documented path. DON items still surface through the WHO News
feed.

**The first run backfills.** RSS uses a 45-day window (a weekly journal's TOC
carries items dated a week or more back, so a short window would silently drop
NEJM and Lancet). Day one therefore ingests ~1,500 records and the digest is
large. From day two only genuinely new items appear, because deduplication
merges everything already seen.

**Preprints are penalised, not excluded** (−3), and labelled as not peer
reviewed in both the digest and the model's system prompt.

---

## Layout

```
idintel/
  collect/    rss.py  pubmed.py  preprints.py  trials.py
  dedupe.py   layered matching + additive merge
  score.py    rule compilation and scoring
  summarize.py  claude -p subprocess, JSON schema, caching
  report.py   Jinja2 → self-contained HTML
  net.py      retry with jittered backoff (NCBI returns transient 5xx)
config/       settings.yaml  feeds.yaml  scoring.yaml
templates/    base + daily/weekly/monthly + shared card macro
systemd/      user units + install.sh
data/         idintel.db
out/          generated reports
```

Appraisals are cached in SQLite and keyed to the record, so a paper is never
sent to the model twice. Failures are split by cause: environmental ones (auth,
timeout, network, rate limit) leave the record **pending** so the next run
retries it, while genuine content failures (unparseable output, missing fields)
are recorded in `summary_error` and skipped so they don't burn tokens forever.
To force the skipped ones to retry:

```bash
./idintel.sh retry
```

AI appraisals are decision support, not clinical advice.
