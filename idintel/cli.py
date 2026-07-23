"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime

from . import report, score, summarize, util
from .collect import COLLECTORS
from .config import Config
from .db import Database, loads
from .dedupe import ingest

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
RED, GREEN, YELLOW = "\033[31m", "\033[32m", "\033[33m"


def _log(message: str) -> None:
    print(f"{DIM}{datetime.now():%H:%M:%S}{RESET} {message}", flush=True)


def _open(args) -> tuple[Config, Database]:
    cfg = Config.load()
    return cfg, Database(cfg.settings.db_path)


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


# ------------------------------------------------------------------ commands

def cmd_collect(args) -> int:
    cfg, db = _open(args)
    started = util.now_iso()
    run_id = db.start_run("collect", started)

    only = set(args.only or [])
    records = []
    per_source = {}

    for name, collector in COLLECTORS.items():
        if only and name not in only:
            continue
        t0 = time.monotonic()
        _log(f"collecting {BOLD}{name}{RESET}…")
        try:
            got = collector(cfg, db)
        except Exception as exc:
            _log(f"  {RED}collector failed{RESET}: {type(exc).__name__}: {exc}")
            got = []
        per_source[name] = len(got)
        records.extend(got)
        _log(f"  {len(got)} records in {time.monotonic() - t0:.1f}s")

    _log(f"deduplicating {len(records)} records…")
    stats = ingest(db, records, cfg)
    _log(f"  {GREEN}{stats['new']} new{RESET}, {stats['merged']} merged, {stats['skipped']} skipped")

    n = score.rescore(db, cfg)
    _log(f"scored {n} records")

    stats["per_source"] = per_source
    db.finish_run(run_id, util.now_iso(), stats)
    db.close()
    return 0


def cmd_summarize(args) -> int:
    cfg, db = _open(args)
    pending = summarize.pending_rows(db, cfg, args.limit)
    if not pending:
        _log("nothing pending appraisal")
        db.close()
        return 0

    _log(f"appraising {len(pending)} records with {BOLD}{cfg.settings.summary_model}{RESET} "
         f"({cfg.settings.summary_workers} workers)…")

    def progress(done, total):
        print(f"\r  {done}/{total}", end="", flush=True)

    stats = summarize.summarize_pending(db, cfg, args.limit, progress)
    print()
    colour = GREEN if stats["failed"] == 0 else YELLOW
    _log(f"  {colour}{stats['ok']} appraised{RESET}, {stats['failed']} failed")
    for error in stats["errors"]:
        _log(f"    {RED}{util.truncate(error, 160)}{RESET}")

    # If everything failed for environmental reasons, say so plainly: the records
    # were left pending (not poisoned) and a rerun after fixing the cause will
    # pick them straight back up.
    if stats["ok"] == 0 and stats.get("retryable", 0) == stats["failed"] and stats["failed"]:
        first = (stats["errors"] or [""])[0].lower()
        if "logged in" in first or "login" in first:
            _log(f"  {YELLOW}The `claude` CLI is not authenticated for headless use.{RESET}")
            _log(f"  {DIM}Fix: run  claude setup-token  (uses your subscription), then rerun.{RESET}")
        else:
            _log(f"  {YELLOW}All failures look environmental (network/auth).{RESET} "
                 f"{DIM}Records left pending; just rerun.{RESET}")
    db.close()
    return 0


def cmd_retry(args) -> int:
    cfg, db = _open(args)
    row = db.one("SELECT COUNT(*) n FROM records WHERE summary_error IS NOT NULL")
    with db.tx() as conn:
        conn.execute("UPDATE records SET summary_error = NULL WHERE summary_error IS NOT NULL")
    _log(f"cleared {row['n']} appraisal error(s); they will be retried next summarize/daily")
    db.close()
    return 0


def cmd_daily(args) -> int:
    rc = cmd_collect(args)
    if rc:
        return rc
    if not args.no_summary:
        cmd_summarize(args)

    cfg, db = _open(args)
    path = report.build_daily(db, cfg, _parse_day(args.day))
    db.close()
    _log(f"{GREEN}daily report{RESET} → {path}")
    print(f"\n  file://{path}\n")
    return 0


def cmd_weekly(args) -> int:
    cfg, db = _open(args)
    path = report.build_weekly(db, cfg, _parse_day(args.day), args.top)
    db.close()
    _log(f"{GREEN}weekly report{RESET} → {path}")
    print(f"\n  file://{path}\n")
    return 0


def cmd_monthly(args) -> int:
    cfg, db = _open(args)
    path = report.build_monthly(db, cfg, _parse_day(args.day))
    db.close()
    _log(f"{GREEN}monthly report{RESET} → {path}")
    print(f"\n  file://{path}\n")
    return 0


def cmd_rescore(args) -> int:
    cfg, db = _open(args)
    n = score.rescore(db, cfg)
    _log(f"rescored {n} records")
    db.close()
    return 0


def cmd_top(args) -> int:
    cfg, db = _open(args)
    rows = db.query(
        """
        SELECT * FROM records
         WHERE substr(first_seen, 1, 10) >= date('now', ?)
         ORDER BY score DESC LIMIT ?
        """,
        (f"-{args.days} day", args.limit),
    )
    for i, row in enumerate(rows, 1):
        stars = "★" * (row["stars"] or 0)
        print(f"{BOLD}{i:>3}.{RESET} [{row['score']:>5.1f}] {stars:<5} {row['title'][:96]}")
        print(f"      {DIM}{row['journal'] or '—'} · {row['priority']} · "
              f"{', '.join(loads(row['sources'], []))}{RESET}")
    db.close()
    return 0


def cmd_doctor(args) -> int:
    cfg, db = _open(args)
    print(f"\n{BOLD}Sources{RESET}")
    rows = db.query("SELECT * FROM feed_state ORDER BY source")
    if not rows:
        print(f"  {DIM}no fetches recorded yet — run `idintel collect`{RESET}")
    broken = 0
    for row in rows:
        if row["last_error"]:
            broken += 1
            print(f"  {RED}✗{RESET} {row['source']:<48} {row['last_error'][:70]}")
        elif args.verbose:
            print(f"  {GREEN}✓{RESET} {row['source']:<48} {row['last_count']} records")
    ok = len(rows) - broken
    print(f"  {GREEN}{ok} healthy{RESET}, {RED if broken else DIM}{broken} failing{RESET}"
          + ("" if args.verbose else f"  {DIM}(-v for all){RESET}"))

    print(f"\n{BOLD}Database{RESET}")
    total = db.one("SELECT COUNT(*) n FROM records")["n"]
    summarised = db.one("SELECT COUNT(*) n FROM records WHERE summary IS NOT NULL")["n"]
    failed = db.one("SELECT COUNT(*) n FROM records WHERE summary_error IS NOT NULL")["n"]
    print(f"  {total} records · {summarised} appraised · {failed} appraisal errors")
    print(f"  {DIM}{cfg.settings.db_path}{RESET}")

    print(f"\n{BOLD}Claude CLI{RESET}")
    t0 = time.monotonic()
    try:
        payload = summarize.call_claude(
            "TITLE: Test record for connectivity check\n\nABSTRACT:\n"
            "A single-centre retrospective cohort of 100 allogeneic HSCT recipients "
            "found breakthrough invasive aspergillosis in 7% on posaconazole prophylaxis.",
            cfg,
        )
        print(f"  {GREEN}✓{RESET} responded in {time.monotonic() - t0:.1f}s "
              f"({cfg.settings.summary_model}), {payload.get('stars')}★")
        print(f"  {DIM}{util.truncate(payload.get('bottom_line', ''), 120)}{RESET}")
    except Exception as exc:
        print(f"  {RED}✗{RESET} {util.truncate(str(exc), 160)}")
        if "logged in" in str(exc).lower():
            print(f"  {DIM}Headless auth is not set up. Run:  {RESET}claude setup-token")
            print(f"  {DIM}(generates a long-lived subscription token a subprocess can use).{RESET}")

    db.close()
    print()
    return 0


def cmd_show(args) -> int:
    cfg, db = _open(args)
    row = db.one("SELECT * FROM records WHERE id = ?", (args.id,))
    if not row:
        print(f"{RED}no record {args.id}{RESET}")
        return 1
    data = {k: row[k] for k in row.keys()}
    for key in ("summary", "score_breakdown", "sources", "authors", "pub_types"):
        data[key] = loads(data.get(key), None)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    db.close()
    return 0


# -------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="idintel", description="ID Intelligence System"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="fetch all sources, dedupe and score")
    p.add_argument("--only", nargs="*", choices=list(COLLECTORS),
                   help="restrict to specific collectors")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("summarize", help="run AI appraisal on high-scoring records")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_summarize)

    p = sub.add_parser("daily", help="collect + appraise + build the daily digest")
    p.add_argument("--day", help="YYYY-MM-DD (default: today)")
    p.add_argument("--only", nargs="*", choices=list(COLLECTORS))
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-summary", action="store_true", help="skip the AI step")
    p.set_defaults(func=cmd_daily)

    p = sub.add_parser("weekly", help="build the top-N must-read report")
    p.add_argument("--day", help="week ending YYYY-MM-DD (default: today)")
    p.add_argument("--top", type=int, default=10)
    p.set_defaults(func=cmd_weekly)

    p = sub.add_parser("monthly", help="build the thematic monthly review")
    p.add_argument("--day", help="month ending YYYY-MM-DD (default: today)")
    p.set_defaults(func=cmd_monthly)

    p = sub.add_parser("rescore", help="re-apply scoring rules to every record")
    p.set_defaults(func=cmd_rescore)

    p = sub.add_parser("retry", help="clear appraisal errors so they retry next run")
    p.set_defaults(func=cmd_retry)

    p = sub.add_parser("top", help="print the highest-scoring recent records")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_top)

    p = sub.add_parser("doctor", help="check source health and Claude connectivity")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("show", help="dump one record as JSON")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
