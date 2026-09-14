"""Generic RSS/Atom collector.

Handles journal tables-of-contents plus every agency source that publishes a
feed (FDA, EMA, CDC, WHO, IDSA, ESCMID). Uses conditional GET so a rerun that
finds nothing new costs one 304 per feed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
import httpx

from .. import util
from ..record import Record

# Namespaced fields publishers use to expose the DOI directly.
_DOI_FIELDS = ("prism_doi", "dc_identifier", "dc_source", "id", "guid")


def _entry_to_record(entry, feed: dict) -> Record | None:
    title = util.strip_html(entry.get("title"))
    if not title:
        return None

    # Abstracts hide in several places depending on the publisher.
    abstract = ""
    for key in ("summary", "description"):
        candidate = util.strip_html(entry.get(key))
        if len(candidate) > len(abstract):
            abstract = candidate
    for block in entry.get("content") or []:
        candidate = util.strip_html(block.get("value"))
        if len(candidate) > len(abstract):
            abstract = candidate

    link = entry.get("link") or ""
    doi_candidates = [entry.get(f) for f in _DOI_FIELDS]
    doi_candidates += [link, abstract]
    doi = util.extract_doi(*doi_candidates)

    published = util.parse_date(
        entry.get("published_parsed")
        or entry.get("updated_parsed")
        or entry.get("published")
        or entry.get("updated")
    )

    authors = []
    for author in entry.get("authors") or []:
        name = util.strip_html(author.get("name"))
        if name:
            authors.append(name)
    if not authors and entry.get("author"):
        authors = [util.strip_html(entry["author"])]
    # Some feeds pack the entire byline into one string; split it into one
    # name per author (see util.split_byline).
    authors = util.split_byline(authors)

    # An RSS "abstract" that merely repeats the title carries no information.
    if abstract and util.norm_title(abstract) == util.norm_title(title):
        abstract = ""

    return Record(
        title=title,
        source=f"RSS: {feed['name']}",
        kind=feed.get("kind", "article"),
        doi=doi,
        pmid=util.extract_pmid(link, entry.get("id") or ""),
        abstract=abstract or None,
        authors=authors,
        journal=feed.get("journal") or feed.get("name"),
        url=link or None,
        published=published,
        pub_types=list(feed.get("pub_types") or []),
    )


def _fetch_one(feed: dict, cfg, prior) -> tuple[dict, list[Record], str | None, dict]:
    """Fetch and parse a single feed. Returns (feed, records, error, http_meta)."""
    headers = {"User-Agent": cfg.settings.user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"}
    if prior:
        if prior["etag"]:
            headers["If-None-Match"] = prior["etag"]
        if prior["last_modified"]:
            headers["If-Modified-Since"] = prior["last_modified"]

    try:
        with httpx.Client(
            timeout=cfg.settings.fetch_timeout, follow_redirects=True
        ) as client:
            resp = client.get(feed["url"], headers=headers)
    except Exception as exc:  # network, DNS, TLS — all non-fatal
        return feed, [], f"{type(exc).__name__}: {exc}", {}

    meta = {
        "etag": resp.headers.get("ETag"),
        "last_modified": resp.headers.get("Last-Modified"),
    }
    if resp.status_code == 304:
        return feed, [], None, meta
    if resp.status_code >= 400:
        return feed, [], f"HTTP {resp.status_code}", meta

    parsed = feedparser.parse(resp.content)
    # bozo means malformed XML; feedparser often still recovers entries, so we
    # only treat it as an error when nothing at all was parsed.
    if parsed.bozo and not parsed.entries:
        return feed, [], f"parse error: {parsed.get('bozo_exception')}", meta

    records = []
    for entry in parsed.entries:
        try:
            record = _entry_to_record(entry, feed)
        except Exception as exc:
            continue
        if record and record.is_usable():
            records.append(record)
    return feed, records, None, meta


def collect_rss(cfg, db) -> list[Record]:
    feeds = cfg.enabled_feeds()
    if not feeds:
        return []

    cutoff = util.today().toordinal() - cfg.settings.rss_lookback_days
    when = util.now_iso()
    out: list[Record] = []

    with ThreadPoolExecutor(max_workers=cfg.settings.fetch_workers) as pool:
        futures = {
            pool.submit(_fetch_one, feed, cfg, db.get_feed_state(f"rss:{feed['name']}")): feed
            for feed in feeds
        }
        for future in as_completed(futures):
            feed, records, error, meta = future.result()
            key = f"rss:{feed['name']}"
            if error:
                db.record_fetch_error(key, when, error)
                continue

            # Many journal feeds carry no usable date; keep those rather than
            # silently dropping a whole publisher.
            fresh = [
                r for r in records
                if r.published is None or r.published.toordinal() >= cutoff
            ]
            db.record_fetch_ok(key, meta.get("etag"), meta.get("last_modified"), when, len(fresh))
            out.extend(fresh)

    return out
