"""Config-driven relevance scoring.

Every rule in ``config/scoring.yaml`` compiles to a regex matcher over selected
fields. A rule fires at most once, contributing its weight. The breakdown is
persisted so the report can show *why* a paper ranked where it did.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import util
from .db import loads

DEFAULT_FIELDS = ("title", "abstract")


@dataclass
class Rule:
    id: str
    label: str
    weight: float
    fields: tuple[str, ...]
    any_of: list[re.Pattern]
    all_of: list[list[re.Pattern]]
    none_of: list[re.Pattern]
    # True for rules that establish ID subject matter (Aspergillus, ESBL, HSCT
    # infection...). Methodology rules (RCT, meta-analysis) and journal tiers are
    # NOT topical: an RCT about wine and driving must not qualify as ID content.
    topical: bool = False

    def matches(self, text: dict[str, str]) -> bool:
        haystack = " ".join(text.get(f, "") for f in self.fields)
        if not haystack.strip():
            return False
        if any(p.search(haystack) for p in self.none_of):
            return False
        if self.any_of and not any(p.search(haystack) for p in self.any_of):
            return False
        for group in self.all_of:
            if not any(p.search(haystack) for p in group):
                return False
        # A rule with neither any_of nor all_of would match everything.
        return bool(self.any_of or self.all_of)


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in (patterns or [])]


def compile_rules(scoring: dict) -> list[Rule]:
    rules = []
    for spec in scoring.get("rules") or []:
        if spec.get("enabled") is False:
            continue
        rules.append(
            Rule(
                id=spec["id"],
                label=spec.get("label", spec["id"]),
                weight=float(spec.get("weight", 0)),
                fields=tuple(spec.get("fields") or DEFAULT_FIELDS),
                any_of=_compile(spec.get("any_of")),
                all_of=[_compile(g) for g in (spec.get("all_of") or [])],
                none_of=_compile(spec.get("none_of")),
                topical=bool(spec.get("topical", False)),
            )
        )
    return rules


def compile_tiers(scoring: dict) -> list[tuple[float, str, list[re.Pattern]]]:
    tiers = []
    for spec in scoring.get("journal_tiers") or []:
        tiers.append(
            (float(spec.get("weight", 0)), spec.get("label", ""), _compile(spec.get("match")))
        )
    return tiers


def score_row(
    row, rules: list[Rule], tiers, scoring: dict
) -> tuple[float, dict, str, bool, str | None]:
    """Return (score, breakdown, priority, topical_hit, journal_tier)."""
    pub_types = loads(row["pub_types"], [])
    text = {
        "title": row["title"] or "",
        "abstract": row["abstract"] or "",
        "journal": row["journal"] or "",
        "pub_types": " ".join(pub_types),
    }

    total = 0.0
    breakdown: dict[str, float] = {}
    topical_hit = False

    for rule in rules:
        if rule.matches(text):
            total += rule.weight
            breakdown[rule.label] = rule.weight
            if rule.topical and rule.weight > 0:
                topical_hit = True

    # Journal tier — highest matching tier only, so a journal can't stack.
    # A journal with no tier match is "Unranked" — the signal for screening out
    # unfamiliar, low-impact or predatory venues.
    journal_norm = util.norm_title(row["journal"] or "")
    best_tier = None
    for weight, label, patterns in tiers:
        if any(p.search(journal_norm) for p in patterns):
            if best_tier is None or weight > best_tier[0]:
                best_tier = (weight, label)
    if best_tier:
        total += best_tier[0]
        breakdown[f"Journal: {best_tier[1]}"] = best_tier[0]
        journal_tier = best_tier[1]
    else:
        journal_tier = "Unranked" if (row["journal"] or "").strip() else None

    # Preprints are useful but carry less weight than peer-reviewed work.
    if row["kind"] == "preprint":
        penalty = float(scoring.get("preprint_penalty", -3))
        total += penalty
        breakdown["Preprint (not peer reviewed)"] = penalty

    # Nothing to read without an abstract; deprioritise but never drop.
    if not (row["abstract"] or "").strip():
        penalty = float(scoring.get("no_abstract_penalty", -4))
        total += penalty
        breakdown["No abstract available"] = penalty

    thresholds = scoring.get("priority") or {}
    if total >= float(thresholds.get("critical", 40)):
        priority = "critical"
    elif total >= float(thresholds.get("high", 25)):
        priority = "high"
    elif total >= float(thresholds.get("medium", 12)):
        priority = "medium"
    else:
        priority = "low"

    return total, breakdown, priority, topical_hit, journal_tier


def rescore(db, cfg, only_unscored: bool = False, since: str | None = None) -> int:
    """(Re)score records. Returns the number of rows updated."""
    rules = compile_rules(cfg.scoring)
    tiers = compile_tiers(cfg.scoring)

    clauses, params = [], []
    if only_unscored:
        clauses.append("score IS NULL")
    if since:
        clauses.append("last_seen >= ?")
        params.append(since)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    rows = db.query(f"SELECT * FROM records {where}", params)
    now = util.now_iso()
    updated = 0

    with db.tx() as conn:
        for row in rows:
            score, breakdown, priority, topical, tier = score_row(
                row, rules, tiers, cfg.scoring
            )
            conn.execute(
                """
                UPDATE records
                   SET score = ?, score_breakdown = ?, priority = ?, scored_at = ?,
                       topical = ?, journal_tier = ?
                 WHERE id = ?
                """,
                (score, json.dumps(breakdown), priority, now,
                 int(topical), tier, row["id"]),
            )
            updated += 1

    return updated
