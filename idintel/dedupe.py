"""Deduplicating ingest.

The same paper reaches us from a journal feed, a PubMed query and sometimes a
preprint server. Matching is layered, strongest signal first:

1. DOI          — authoritative
2. PMID         — authoritative
3. NCT id       — authoritative for trials
4. exact normalised title
5. fuzzy title within a blocking bucket

Merging is *additive*: we never lose an abstract or a source by overwriting it
with a thinner copy.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

from . import util
from .db import loads
from .record import Record

# A preprint and its published version have different DOIs; when the journal
# version arrives we want to keep the journal metadata, not the preprint's.
_PREPRINT_JOURNAL_HINT = "preprint"


def _find_existing(db, record: Record, cutoff: str):
    for column, value in (("doi", record.doi), ("pmid", record.pmid), ("nct", record.nct)):
        if value:
            row = db.one(f"SELECT * FROM records WHERE {column} = ?", (value,))
            if row:
                return row

    title_norm = record.title_norm
    row = db.one(
        "SELECT * FROM records WHERE title_norm = ? AND last_seen >= ? LIMIT 1",
        (title_norm, cutoff),
    )
    if row:
        return row

    # Fuzzy pass, restricted to the blocking bucket so this stays O(bucket).
    threshold = getattr(_find_existing, "threshold", 0.90)
    candidates = db.query(
        "SELECT * FROM records WHERE block_key = ? AND last_seen >= ?",
        (record.block_key, cutoff),
    )
    best, best_score = None, 0.0
    for candidate in candidates:
        score = util.similarity(title_norm, candidate["title_norm"])
        if score > best_score:
            best, best_score = candidate, score
    if best is not None and best_score >= threshold:
        return best
    return None


def _merge(db, row, record: Record, now: str) -> None:
    updates: dict[str, object] = {"last_seen": now}

    # Identifiers: only ever fill a gap, never overwrite.
    for column, value in (("doi", record.doi), ("pmid", record.pmid), ("nct", record.nct)):
        if value and not row[column]:
            updates[column] = value

    # Keep whichever abstract carries more information.
    incoming_abstract = record.abstract or ""
    if len(incoming_abstract) > len(row["abstract"] or ""):
        updates["abstract"] = incoming_abstract
        # A materially better abstract deserves a fresh score.
        updates["score"] = None

    existing_journal = row["journal"] or ""
    if record.journal:
        # Promote a real journal name over a preprint placeholder.
        if not existing_journal or _PREPRINT_JOURNAL_HINT in existing_journal.lower():
            if _PREPRINT_JOURNAL_HINT not in record.journal.lower() or not existing_journal:
                updates["journal"] = record.journal
                if row["kind"] == "preprint" and _PREPRINT_JOURNAL_HINT not in record.journal.lower():
                    updates["kind"] = "article"

    existing_authors = loads(row["authors"], [])
    if len(record.authors) > len(existing_authors):
        updates["authors"] = json.dumps(record.authors)

    # Prefer a resolvable link; a DOI or PubMed URL beats a publisher redirect.
    if record.url and not row["url"]:
        updates["url"] = record.url

    if record.published and (
        not row["published"] or record.published.isoformat() < row["published"]
    ):
        updates["published"] = record.published.isoformat()

    pub_types = sorted(set(loads(row["pub_types"], [])) | set(record.pub_types))
    if pub_types != sorted(set(loads(row["pub_types"], []))):
        updates["pub_types"] = json.dumps(pub_types)
        updates["score"] = None

    sources = loads(row["sources"], [])
    if record.source not in sources:
        sources.append(record.source)
        updates["sources"] = json.dumps(sources)

    if len(updates) == 1:  # only last_seen
        with db.tx() as conn:
            conn.execute("UPDATE records SET last_seen = ? WHERE id = ?", (now, row["id"]))
        return

    def apply(payload: dict) -> None:
        assignments = ", ".join(f"{k} = ?" for k in payload)
        with db.tx() as conn:
            conn.execute(
                f"UPDATE records SET {assignments} WHERE id = ?",
                (*payload.values(), row["id"]),
            )

    try:
        apply(updates)
    except sqlite3.IntegrityError:
        # Another row already owns this DOI/PMID — the two rows are near-duplicates
        # that the title matcher split. Keep the non-identifier content rather than
        # losing the merge entirely; the identifier stays with the row that has it.
        reduced = {k: v for k, v in updates.items() if k not in ("doi", "pmid", "nct")}
        if reduced:
            apply(reduced)


def _insert(db, record: Record, now: str) -> None:
    with db.tx() as conn:
        conn.execute(
            """
            INSERT INTO records (kind, doi, pmid, nct, title, title_norm, block_key,
                                 abstract, authors, journal, url, published,
                                 first_seen, last_seen, pub_types, sources)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.kind, record.doi, record.pmid, record.nct,
                record.title, record.title_norm, record.block_key,
                record.abstract, json.dumps(record.authors), record.journal,
                record.url, util.iso(record.published), now, now,
                json.dumps(record.pub_types), json.dumps([record.source]),
            ),
        )


def ingest(db, records: list[Record], cfg) -> dict:
    """Insert or merge every record. Returns counters for the run log."""
    _find_existing.threshold = cfg.settings.dedupe_threshold
    now = util.now_iso()
    cutoff = (
        util.today() - timedelta(days=cfg.settings.dedupe_window_days)
    ).isoformat()

    stats = {"seen": len(records), "new": 0, "merged": 0, "skipped": 0}

    for record in records:
        if not record.is_usable():
            stats["skipped"] += 1
            continue
        try:
            existing = _find_existing(db, record, cutoff)
            if existing is not None:
                _merge(db, existing, record, now)
                stats["merged"] += 1
            else:
                _insert(db, record, now)
                stats["new"] += 1
        except Exception:
            # A single malformed record must not abort the morning run.
            stats["skipped"] += 1

    return stats
