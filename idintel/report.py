"""HTML report generation (daily digest, weekly must-read, monthly review)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import util
from .config import ROOT
from .db import loads
from .score import compile_rules

TEMPLATE_DIR = ROOT / "templates"

PRIORITY_ORDER = ["critical", "high", "medium", "low"]
PRIORITY_LABEL = {
    "critical": "Critical",
    "high": "High priority",
    "medium": "Worth a look",
    "low": "Everything else",
}

# Maps a journal-tier label to a CSS class for the badge. "Unranked" is the
# amber screening signal for unfamiliar / low-impact / predatory venues.
TIER_CLASS = {
    "Top general": "tier-top",
    "Agency / society": "tier-top",
    "Core ID": "tier-core",
    "Specialist": "tier-spec",
    "Unranked": "tier-unranked",
}


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["stars"] = lambda n: "★" * int(n or 0) + "☆" * (5 - int(n or 0))
    env.filters["truncate_words"] = lambda s, n=320: util.truncate(s or "", n)
    return env


def view(row) -> dict:
    """Turn a database row into a template-friendly dict."""
    summary = loads(row["summary"], None)
    authors = loads(row["authors"], [])
    sources = loads(row["sources"], [])
    breakdown = loads(row["score_breakdown"], {})

    url = row["url"]
    if not url and row["doi"]:
        url = f"https://doi.org/{row['doi']}"

    tier = row["journal_tier"]
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "journal": row["journal"] or "—",
        "journal_tier": tier,
        "journal_tier_class": TIER_CLASS.get(tier, "tier-none"),
        "authors": authors,
        "author_line": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
        "url": url,
        "doi": row["doi"],
        "pmid": row["pmid"],
        "nct": row["nct"],
        "published": row["published"],
        "abstract": row["abstract"],
        "score": round(row["score"] or 0, 1),
        "priority": row["priority"] or "low",
        "topical": bool(row["topical"]),
        "breakdown": sorted(breakdown.items(), key=lambda kv: -kv[1]),
        "sources": sources,
        "n_sources": len(sources),
        "summary": summary,
        "stars": row["stars"] or (summary or {}).get("stars"),
        "summary_error": row["summary_error"],
    }


def _write(env: Environment, template: str, context: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    html = env.get_template(template).render(**context)
    path.write_text(html, encoding="utf-8")
    return path


def _link_latest(target: Path, alias: Path) -> None:
    """Maintain a stable ``latest-*.html`` pointer next to the archive."""
    try:
        if alias.exists() or alias.is_symlink():
            alias.unlink()
        alias.symlink_to(target.relative_to(alias.parent))
    except OSError:
        # Symlinks may be unavailable; a copy is an acceptable fallback.
        alias.write_bytes(target.read_bytes())


# --------------------------------------------------------------------- daily

def build_daily(db, cfg, day: date | None = None) -> Path:
    day = day or util.today()
    rows = db.query(
        """
        SELECT * FROM records
         WHERE substr(first_seen, 1, 10) = ?
         ORDER BY score DESC, published DESC
        """,
        (day.isoformat(),),
    )
    items = [view(r) for r in rows]

    # Relevance gate: a record only belongs in the digest if it hit at least one
    # topical ID rule. This drops the ~60% of collected records that score only
    # on methodology or journal tier — e.g. an RCT about wine and driving, which
    # earned points for being an RCT but has nothing to do with infection.
    # A high-scoring record is kept regardless, as a safety net against a topical
    # rule gap. Both are configurable.
    require_topical = cfg.settings.report.get("daily_require_topical", True)
    keep_floor = float(cfg.settings.report.get("daily_keep_score", 25))
    excluded = 0
    if require_topical:
        kept = [i for i in items if i["topical"] or i["score"] >= keep_floor]
        excluded = len(items) - len(kept)
        items = kept

    # Without a cap the first run (which backfills the whole RSS window) renders
    # thousands of cards and becomes unreadable. 0 means unlimited.
    caps = cfg.settings.report.get("daily_max_per_group") or {}
    groups = []
    for priority in PRIORITY_ORDER:
        bucket = [i for i in items if i["priority"] == priority]
        if not bucket:
            continue
        cap = int(caps.get(priority, 0))
        shown, hidden = bucket, 0
        if cap and len(bucket) > cap:
            shown, hidden = bucket[:cap], len(bucket) - cap
        groups.append({
            "key": priority,
            "label": PRIORITY_LABEL[priority],
            "items": shown,
            "total": len(bucket),
            "hidden": hidden,
        })

    feed_errors = db.query(
        "SELECT source, last_error, last_fetch FROM feed_state "
        "WHERE last_error IS NOT NULL ORDER BY source"
    )

    context = {
        "day": day,
        "generated": util.now_iso(),
        "groups": groups,
        "total": len(items),
        "excluded": excluded,
        "n_summarised": sum(1 for i in items if i["summary"]),
        "feed_errors": [dict(r) for r in feed_errors],
        "title": f"ID Intelligence — {day:%A %d %B %Y}",
        "subtitle": "Daily digest",
    }

    out = cfg.settings.out_dir / "daily" / f"{day.isoformat()}.html"
    path = _write(_env(), "daily.html.j2", context, out)
    _link_latest(path, cfg.settings.out_dir / "latest-daily.html")
    return path


# -------------------------------------------------------------------- weekly

def build_weekly(db, cfg, end: date | None = None, top_n: int = 10) -> Path:
    end = end or util.today()
    start = end - timedelta(days=6)
    rows = db.query(
        """
        SELECT * FROM records
         WHERE substr(first_seen, 1, 10) BETWEEN ? AND ?
         ORDER BY score DESC
         LIMIT ?
        """,
        (start.isoformat(), end.isoformat(), top_n),
    )
    total = db.one(
        "SELECT COUNT(*) AS n FROM records WHERE substr(first_seen, 1, 10) BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    )

    context = {
        "start": start,
        "end": end,
        "generated": util.now_iso(),
        "items": [view(r) for r in rows],
        "week_total": total["n"] if total else 0,
        "title": f"Must read — week ending {end:%d %B %Y}",
        "subtitle": f"Top {top_n} of the week",
    }

    out = cfg.settings.out_dir / "weekly" / f"{end.isoformat()}.html"
    path = _write(_env(), "weekly.html.j2", context, out)
    _link_latest(path, cfg.settings.out_dir / "latest-weekly.html")
    return path


# ------------------------------------------------------------------- monthly

def build_monthly(db, cfg, end: date | None = None, per_theme: int = 8) -> Path:
    end = end or util.today()
    start = end - timedelta(days=30)
    rows = db.query(
        """
        SELECT * FROM records
         WHERE substr(first_seen, 1, 10) BETWEEN ? AND ?
         ORDER BY score DESC
        """,
        (start.isoformat(), end.isoformat()),
    )

    # Themes reuse the scoring rule matcher, so they are configured the same way.
    theme_rules = compile_rules({"rules": cfg.scoring.get("themes") or []})

    sections = []
    for rule in theme_rules:
        bucket = []
        for row in rows:
            text = {
                "title": row["title"] or "",
                "abstract": row["abstract"] or "",
                "journal": row["journal"] or "",
                "pub_types": " ".join(loads(row["pub_types"], [])),
            }
            if rule.matches(text):
                bucket.append(view(row))
            if len(bucket) >= per_theme:
                break
        if bucket:
            sections.append({"label": rule.label, "items": bucket})

    context = {
        "start": start,
        "end": end,
        "generated": util.now_iso(),
        "sections": sections,
        "month_total": len(rows),
        "title": f"Monthly review — {end:%B %Y}",
        "subtitle": f"{start:%d %b} to {end:%d %b %Y}",
    }

    out = cfg.settings.out_dir / "monthly" / f"{end.isoformat()}.html"
    path = _write(_env(), "monthly.html.j2", context, out)
    _link_latest(path, cfg.settings.out_dir / "latest-monthly.html")
    return path
