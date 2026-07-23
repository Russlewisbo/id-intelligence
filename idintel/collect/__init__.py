"""Source collectors.

Each collector is a callable ``(cfg, db) -> list[Record]``. They must never
raise: a dead feed degrades that source only, and the failure is recorded in
``feed_state`` for ``idintel doctor``.
"""

from __future__ import annotations

from .preprints import collect_preprints
from .pubmed import collect_pubmed
from .rss import collect_rss
from .trials import collect_trials

COLLECTORS = {
    "rss": collect_rss,
    "pubmed": collect_pubmed,
    "preprints": collect_preprints,
    "trials": collect_trials,
}

__all__ = ["COLLECTORS", "collect_rss", "collect_pubmed", "collect_preprints", "collect_trials"]
