"""Abstract appraisal via the Claude Code CLI in headless mode.

Runs ``claude -p`` as a subprocess with a JSON schema so the output is
structured rather than prose we have to parse. Results are cached in SQLite,
so a paper is never appraised twice.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import util
from .db import loads

SCHEMA = {
    "type": "object",
    "properties": {
        "bottom_line": {
            "type": "string",
            "description": "One or two sentences: what a clinician should now do or believe differently.",
        },
        "why_it_matters": {
            "type": "string",
            "description": "Two to three sentences placing the finding in current ID practice.",
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-4 concrete methodological strengths.",
        },
        "weaknesses": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-4 concrete limitations that temper the conclusion.",
        },
        "read_full": {
            "type": "string",
            "enum": ["yes", "skim", "no"],
            "description": "Whether the full text is worth the reader's time.",
        },
        "read_full_reason": {"type": "string"},
        "stars": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "Overall importance to an ID/transplant-ID clinician.",
        },
        "topics": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-5 short topic tags, e.g. 'invasive aspergillosis', 'PK/PD'.",
        },
        "design": {
            "type": "string",
            "description": "Study design in a few words, e.g. 'multicentre RCT, n=527'.",
        },
    },
    "required": [
        "bottom_line", "why_it_matters", "strengths", "weaknesses",
        "read_full", "stars", "topics", "design",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are a senior infectious diseases physician-scientist with \
expertise in transplant ID, medical mycology, antimicrobial resistance and \
antimicrobial PK/PD. You appraise abstracts for a busy consultant colleague.

Rules:
- Judge only what the supplied text supports. Never invent numbers, sample \
sizes, endpoints or funding that are not stated.
- If the text is a title only, or too thin to appraise, say so plainly in \
bottom_line, set stars to 1 and read_full to "no".
- Be specific and quantitative where the abstract is quantitative. Prefer \
"28-day mortality 19% vs 27%, HR 0.71" over "improved outcomes".
- Weaknesses must be real methodological critiques (confounding, power, \
surrogate endpoints, external validity, industry funding), not filler.
- Stars: 5 = practice-changing; 4 = important, may change management in a \
subgroup; 3 = solid, worth knowing; 2 = narrow or preliminary; 1 = minimal \
clinical relevance.
- Preprints are not peer reviewed; weight them accordingly."""


class SummaryError(RuntimeError):
    pass


# Failures that are about the environment, not the paper. These must NOT be
# persisted as summary_error, or a single broken-auth or network-down run would
# permanently poison every high-scoring record and nothing would retry once the
# problem is fixed. Genuine content failures (unparseable JSON, missing fields)
# are persisted so we don't burn tokens re-appraising a hopeless abstract.
_RETRYABLE_MARKERS = (
    "not logged in",
    "please run /login",
    "timed out",
    "cli not found",
    "connecterror",
    "connection",
    "name resolution",
    "overloaded",
    "rate limit",
    "429",
    "500", "502", "503", "504",
    "bad envelope",  # a truncated/non-JSON envelope usually means a killed process
)


def is_retryable(error: str) -> bool:
    low = (error or "").lower()
    return any(marker in low for marker in _RETRYABLE_MARKERS)


def _child_env(cfg) -> dict:
    env = dict(os.environ)
    if cfg.settings.summary.get("clean_env", True):
        # A parent agent session may point the CLI at a proxy endpoint; drop
        # those so the child authenticates with the user's own credentials.
        for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_SSE_PORT",
                    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            env.pop(key, None)
    return env


def build_prompt(row) -> str:
    sources = ", ".join(loads(row["sources"], [])) or "unknown"
    pub_types = ", ".join(loads(row["pub_types"], [])) or "not stated"
    authors = loads(row["authors"], [])
    author_line = ", ".join(authors[:6]) + (" et al." if len(authors) > 6 else "")
    abstract = (row["abstract"] or "").strip() or "(No abstract was supplied by the source.)"

    return f"""Appraise the following record.

TITLE: {row['title']}
JOURNAL / SOURCE: {row['journal'] or 'unknown'} ({sources})
PUBLICATION TYPES: {pub_types}
AUTHORS: {author_line or 'not listed'}
PUBLISHED: {row['published'] or 'unknown'}
RECORD TYPE: {row['kind']}

ABSTRACT:
{abstract}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    # Fall back to the first balanced object in the response.
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except ValueError as exc:
            raise SummaryError(f"unparseable JSON: {exc}") from exc
    raise SummaryError(f"no JSON in response: {util.truncate(text, 200)}")


def call_claude(prompt: str, cfg) -> dict:
    """Invoke the CLI once and return the parsed appraisal."""
    cmd = [
        "claude", "-p",
        "--model", cfg.settings.summary_model,
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
        "--system-prompt", SYSTEM_PROMPT,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--disallowed-tools", "Bash,Edit,Write,Read,WebFetch,WebSearch,Task,Glob,Grep",
    ]

    # Run outside the project so no CLAUDE.md or repo context leaks into the
    # appraisal and inflates token cost.
    with tempfile.TemporaryDirectory(prefix="idintel-") as workdir:
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=cfg.settings.summary_timeout,
                env=_child_env(cfg),
                cwd=workdir,
            )
        except subprocess.TimeoutExpired as exc:
            raise SummaryError(f"timed out after {cfg.settings.summary_timeout}s") from exc
        except FileNotFoundError as exc:
            raise SummaryError("`claude` CLI not found on PATH") from exc

    if proc.returncode != 0:
        raise SummaryError(
            f"exit {proc.returncode}: {util.truncate(proc.stderr or proc.stdout, 300)}"
        )

    try:
        envelope = json.loads(proc.stdout)
    except ValueError as exc:
        raise SummaryError(f"bad envelope: {util.truncate(proc.stdout, 200)}") from exc

    result = envelope.get("result", "")
    if envelope.get("is_error"):
        raise SummaryError(f"CLI error: {util.truncate(str(result), 300)}")

    payload = result if isinstance(result, dict) else _extract_json(str(result))

    missing = [k for k in SCHEMA["required"] if k not in payload]
    if missing:
        raise SummaryError(f"missing fields: {', '.join(missing)}")
    return payload


def pending_rows(db, cfg, limit: int | None = None) -> list:
    limit = cfg.settings.summary_max_per_run if limit is None else limit
    return db.query(
        """
        SELECT * FROM records
         WHERE summary IS NULL
           AND score >= ?
           AND (summary_error IS NULL OR summary_error = '')
         ORDER BY score DESC
         LIMIT ?
        """,
        (cfg.settings.summary_min_score, limit),
    )


def _summarize_one(row, cfg) -> tuple[int, dict | None, str | None]:
    try:
        return row["id"], call_claude(build_prompt(row), cfg), None
    except SummaryError as exc:
        return row["id"], None, str(exc)
    except Exception as exc:
        return row["id"], None, f"{type(exc).__name__}: {exc}"


def summarize_pending(db, cfg, limit: int | None = None, progress=None) -> dict:
    rows = pending_rows(db, cfg, limit)
    stats = {"attempted": len(rows), "ok": 0, "failed": 0, "errors": []}
    if not rows:
        return stats

    now = util.now_iso()
    model = cfg.settings.summary_model

    with ThreadPoolExecutor(max_workers=cfg.settings.summary_workers) as pool:
        futures = [pool.submit(_summarize_one, row, cfg) for row in rows]
        for done, future in enumerate(as_completed(futures), 1):
            record_id, payload, error = future.result()
            if payload:
                with db.tx() as conn:
                    conn.execute(
                        """
                        UPDATE records
                           SET summary = ?, summary_at = ?, summary_model = ?,
                               stars = ?, summary_error = NULL
                         WHERE id = ?
                        """,
                        (json.dumps(payload), now, model,
                         int(payload.get("stars", 3)), record_id),
                    )
                stats["ok"] += 1
            else:
                retryable = is_retryable(error)
                if not retryable:
                    # Persist only genuine content failures so they are skipped
                    # next run. Environmental failures are left pending.
                    with db.tx() as conn:
                        conn.execute(
                            "UPDATE records SET summary_error = ? WHERE id = ?",
                            (error[:500], record_id),
                        )
                stats["failed"] += 1
                stats["retryable"] = stats.get("retryable", 0) + int(retryable)
                if len(stats["errors"]) < 5:
                    stats["errors"].append(error)
            if progress:
                progress(done, len(rows))

    return stats
