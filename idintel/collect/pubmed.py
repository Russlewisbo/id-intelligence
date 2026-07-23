"""PubMed collector via NCBI E-utilities.

Preferred over PubMed's RSS export because efetch returns the structured
abstract, publication types (MeSH) and the DOI — all of which the scorer and
deduplicator depend on.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import httpx

from .. import util
from ..net import request_with_retry
from ..record import Record

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# NCBI allows 3 requests/second unauthenticated, 10/s with an API key.
_THROTTLE_NO_KEY = 0.36
_THROTTLE_KEY = 0.11


def _text(node) -> str:
    """Flatten an element including inline markup (<i>, <sup>, ...)."""
    return util.strip_html("".join(node.itertext())) if node is not None else ""


def _parse_article(article: ET.Element) -> Record | None:
    citation = article.find("MedlineCitation")
    if citation is None:
        return None

    pmid = _text(citation.find("PMID")) or None
    art = citation.find("Article")
    if art is None:
        return None

    title = _text(art.find("ArticleTitle"))
    if not title:
        return None

    # Structured abstracts arrive as several labelled AbstractText nodes.
    parts = []
    for node in art.findall("./Abstract/AbstractText"):
        body = _text(node)
        if not body:
            continue
        label = node.get("Label")
        parts.append(f"{label.strip().title()}: {body}" if label else body)
    abstract = " ".join(parts) or None

    journal = _text(art.find("./Journal/ISOAbbreviation")) or _text(
        art.find("./Journal/Title")
    )

    authors = []
    for author in art.findall("./AuthorList/Author"):
        last = _text(author.find("LastName"))
        initials = _text(author.find("Initials"))
        collective = _text(author.find("CollectiveName"))
        if last:
            authors.append(f"{last} {initials}".strip())
        elif collective:
            authors.append(collective)

    pub_types = [
        _text(node) for node in art.findall("./PublicationTypeList/PublicationType")
    ]

    doi = None
    for aid in article.findall("./PubmedData/ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = util.norm_doi(_text(aid))
            break
    if not doi:
        for eloc in art.findall("ELocationID"):
            if eloc.get("EIdType") == "doi":
                doi = util.norm_doi(_text(eloc))
                break

    # Prefer the electronic publication date, fall back to the journal issue.
    published = None
    for path in (
        "./ArticleDate",
        "./Journal/JournalIssue/PubDate",
    ):
        node = art.find(path)
        if node is None:
            continue
        year = _text(node.find("Year"))
        if not year:
            medline = _text(node.find("MedlineDate"))
            published = util.parse_date(medline[:4]) if medline else None
            if published:
                break
            continue
        month = _text(node.find("Month")) or "1"
        day = _text(node.find("Day")) or "1"
        published = util.parse_date(f"{year} {month} {day}") or util.parse_date(
            f"{year}-{int(month) if month.isdigit() else 1:02d}-{int(day) if day.isdigit() else 1:02d}"
        )
        if published:
            break

    return Record(
        title=title,
        source="PubMed",
        kind="article",
        doi=doi,
        pmid=pmid,
        abstract=abstract,
        authors=authors,
        journal=journal or None,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
        published=published,
        pub_types=pub_types,
    )


def _run_query(client: httpx.Client, query: dict, cfg, api_key: str | None,
               throttle: float) -> tuple[list[Record], str | None]:
    reldate = int(query.get("reldate", cfg.settings.lookback_days))
    retmax = int(query.get("retmax", cfg.settings.pubmed.get("retmax", 200)))

    search_params = {
        "db": "pubmed",
        "term": query["term"],
        "retmax": retmax,
        "retmode": "json",
        "reldate": reldate,
        "datetype": query.get("datetype", "edat"),
        "sort": "pub_date",
    }
    if api_key:
        search_params["api_key"] = api_key

    try:
        resp = request_with_retry(client, "GET", ESEARCH, params=search_params)
        pmids = resp.json().get("esearchresult", {}).get("idlist", [])
    except Exception as exc:
        return [], f"esearch failed: {type(exc).__name__}: {exc}"

    if not pmids:
        return [], None

    records: list[Record] = []
    # efetch caps out well below this, but 200 ids per POST is comfortable.
    for start in range(0, len(pmids), 200):
        batch = pmids[start : start + 200]
        time.sleep(throttle)
        fetch_params = {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"}
        if api_key:
            fetch_params["api_key"] = api_key
        try:
            resp = request_with_retry(client, "POST", EFETCH, data=fetch_params)
            root = ET.fromstring(resp.content)
        except Exception as exc:
            return records, f"efetch failed: {type(exc).__name__}: {exc}"

        for article in root.findall(".//PubmedArticle"):
            try:
                record = _parse_article(article)
            except Exception:
                continue
            if record and record.is_usable():
                record.source = f"PubMed: {query['name']}"
                records.append(record)

    return records, None


def collect_pubmed(cfg, db) -> list[Record]:
    queries = cfg.settings.pubmed.get("queries") or []
    if not queries:
        return []

    api_key = cfg.settings.pubmed.get("api_key") or None
    throttle = _THROTTLE_KEY if api_key else _THROTTLE_NO_KEY
    when = util.now_iso()
    out: list[Record] = []

    headers = {"User-Agent": cfg.settings.user_agent}
    with httpx.Client(timeout=cfg.settings.fetch_timeout, headers=headers,
                      follow_redirects=True) as client:
        for query in queries:
            if query.get("enabled") is False:
                continue
            key = f"pubmed:{query['name']}"
            time.sleep(throttle)
            records, error = _run_query(client, query, cfg, api_key, throttle)
            if error:
                db.record_fetch_error(key, when, error)
            else:
                db.record_fetch_ok(key, None, None, when, len(records))
            out.extend(records)

    return out
