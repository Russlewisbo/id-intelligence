"""bioRxiv and medRxiv collector.

The public API only filters by date interval, so we page through the window and
filter locally by subject category and keyword.
"""

from __future__ import annotations

import re
from datetime import timedelta

import httpx

from .. import util
from ..net import request_with_retry
from ..record import Record

API = "https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"
PAGE = 100
MAX_PAGES = 60  # hard stop so a bad date window can't spin forever


def _matches(item: dict, categories: set[str], patterns: list[re.Pattern]) -> bool:
    category = (item.get("category") or "").strip().lower()
    if categories and category in categories:
        return True
    if not patterns:
        return False
    haystack = f"{item.get('title', '')} {item.get('abstract', '')}"
    return any(p.search(haystack) for p in patterns)


def _to_record(item: dict, server: str) -> Record | None:
    title = item.get("title")
    if not title:
        return None
    doi = util.norm_doi(item.get("doi"))
    authors = [a.strip() for a in (item.get("authors") or "").split(";") if a.strip()]
    label = "bioRxiv" if server == "biorxiv" else "medRxiv"

    return Record(
        title=title,
        source=label,
        kind="preprint",
        doi=doi,
        abstract=item.get("abstract") or None,
        authors=authors,
        journal=f"{label} (preprint, not peer reviewed)",
        url=f"https://doi.org/{doi}" if doi else None,
        published=util.parse_date(item.get("date")),
        pub_types=["Preprint"],
    )


def _collect_server(server: str, cfg, db, categories: set[str],
                    patterns: list[re.Pattern]) -> list[Record]:
    end = util.today()
    start = end - timedelta(days=max(cfg.settings.lookback_days, 1))
    when = util.now_iso()
    key = f"preprints:{server}"

    records: list[Record] = []
    seen_dois: set[str] = set()
    cursor = 0
    headers = {"User-Agent": cfg.settings.user_agent}

    try:
        with httpx.Client(timeout=cfg.settings.fetch_timeout, headers=headers,
                          follow_redirects=True) as client:
            for _ in range(MAX_PAGES):
                url = API.format(
                    server=server, start=start.isoformat(),
                    end=end.isoformat(), cursor=cursor,
                )
                resp = request_with_retry(client, "GET", url)
                payload = resp.json()
                collection = payload.get("collection") or []
                if not collection:
                    break

                for item in collection:
                    if not _matches(item, categories, patterns):
                        continue
                    record = _to_record(item, server)
                    if not record or not record.is_usable():
                        continue
                    # The API returns every version; keep the newest only.
                    if record.doi and record.doi in seen_dois:
                        continue
                    if record.doi:
                        seen_dois.add(record.doi)
                    records.append(record)

                messages = payload.get("messages") or [{}]
                total = int(messages[0].get("total", 0) or 0)
                cursor += PAGE
                if cursor >= total:
                    break
    except Exception as exc:
        db.record_fetch_error(key, when, f"{type(exc).__name__}: {exc}")
        return records

    db.record_fetch_ok(key, None, None, when, len(records))
    return records


def collect_preprints(cfg, db) -> list[Record]:
    conf = cfg.settings.preprints
    if not conf.get("enabled", True):
        return []

    categories = {c.strip().lower() for c in (conf.get("categories") or [])}
    patterns = [
        re.compile(p, re.I) for p in (conf.get("keywords") or [])
    ]

    out: list[Record] = []
    for server in conf.get("servers", ["biorxiv", "medrxiv"]):
        out.extend(_collect_server(server, cfg, db, categories, patterns))
    return out
