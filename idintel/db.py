"""SQLite storage layer.

One row per *deduplicated* record. Sources, scores and AI summaries are all
persisted so a rerun never re-does work that has already been paid for.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS records (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL DEFAULT 'article',
    doi             TEXT,
    pmid            TEXT,
    nct             TEXT,
    title           TEXT NOT NULL,
    title_norm      TEXT NOT NULL,
    block_key       TEXT NOT NULL,
    abstract        TEXT,
    authors         TEXT,
    journal         TEXT,
    url             TEXT,
    published       TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    pub_types       TEXT NOT NULL DEFAULT '[]',
    sources         TEXT NOT NULL DEFAULT '[]',
    score           REAL,
    score_breakdown TEXT,
    priority        TEXT,
    scored_at       TEXT,
    summary         TEXT,
    summary_at      TEXT,
    summary_model   TEXT,
    stars           INTEGER,
    summary_error   TEXT,
    topical         INTEGER NOT NULL DEFAULT 0,
    journal_tier    TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_records_doi  ON records(doi)  WHERE doi  IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_records_pmid ON records(pmid) WHERE pmid IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_records_block      ON records(block_key);
CREATE INDEX IF NOT EXISTS ix_records_title      ON records(title_norm);
CREATE INDEX IF NOT EXISTS ix_records_first_seen ON records(first_seen);
CREATE INDEX IF NOT EXISTS ix_records_score      ON records(score DESC);
CREATE INDEX IF NOT EXISTS ix_records_published  ON records(published);

-- Conditional-GET bookkeeping and per-source health.
CREATE TABLE IF NOT EXISTS feed_state (
    source        TEXT PRIMARY KEY,
    etag          TEXT,
    last_modified TEXT,
    last_fetch    TEXT,
    last_ok       TEXT,
    last_error    TEXT,
    ok_count      INTEGER NOT NULL DEFAULT 0,
    err_count     INTEGER NOT NULL DEFAULT 0,
    last_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id       INTEGER PRIMARY KEY,
    kind     TEXT NOT NULL,
    started  TEXT NOT NULL,
    finished TEXT,
    stats    TEXT
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so new
        columns must be added explicitly. Each entry is (column, DDL type).
        """
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(records)")}
        additions = {
            "topical": "INTEGER NOT NULL DEFAULT 0",
            "journal_tier": "TEXT",
        }
        for column, ddl in additions.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE records ADD COLUMN {column} {ddl}")

    # ------------------------------------------------------------------ core

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    # ------------------------------------------------------------ feed state

    def get_feed_state(self, source: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM feed_state WHERE source = ?", (source,))

    def record_fetch_ok(
        self, source: str, etag: str | None, last_modified: str | None,
        when: str, count: int,
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO feed_state (source, etag, last_modified, last_fetch,
                                        last_ok, last_error, ok_count, last_count)
                VALUES (?, ?, ?, ?, ?, NULL, 1, ?)
                ON CONFLICT(source) DO UPDATE SET
                    etag          = excluded.etag,
                    last_modified = excluded.last_modified,
                    last_fetch    = excluded.last_fetch,
                    last_ok       = excluded.last_ok,
                    last_error    = NULL,
                    ok_count      = feed_state.ok_count + 1,
                    last_count    = excluded.last_count
                """,
                (source, etag, last_modified, when, when, count),
            )

    def record_fetch_error(self, source: str, when: str, error: str) -> None:
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO feed_state (source, last_fetch, last_error, err_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(source) DO UPDATE SET
                    last_fetch = excluded.last_fetch,
                    last_error = excluded.last_error,
                    err_count  = feed_state.err_count + 1
                """,
                (source, when, error[:500]),
            )

    # ------------------------------------------------------------------ runs

    def start_run(self, kind: str, started: str) -> int:
        with self.tx() as conn:
            cur = conn.execute(
                "INSERT INTO runs (kind, started) VALUES (?, ?)", (kind, started)
            )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, finished: str, stats: dict) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE runs SET finished = ?, stats = ? WHERE id = ?",
                (finished, json.dumps(stats), run_id),
            )


def loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default
