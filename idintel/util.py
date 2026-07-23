"""Text normalisation, identifier extraction and date parsing helpers.

Everything here is deliberately dependency-free so the dedup logic stays cheap
and testable.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime, timezone

# A DOI is 10.<registrant>/<suffix>. The suffix is permissive, so we trim
# trailing punctuation that commonly gets glued on by RSS descriptions.
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>&\]\[]+)", re.I)
PMID_RE = re.compile(r"(?:pubmed[/:]?|pmid[:\s]*)(\d{6,9})", re.I)
NCT_RE = re.compile(r"\b(NCT\d{8})\b", re.I)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TRAILING_PUNCT = ".,;:)]}>\"'"


def strip_html(value: str | None) -> str:
    """Flatten an HTML fragment to plain text."""
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def norm_doi(value: str | None) -> str | None:
    """Canonicalise a DOI: bare, lowercase, no resolver prefix."""
    if not value:
        return None
    text = strip_html(value).strip()
    text = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    match = DOI_RE.search(text)
    if not match:
        return None
    doi = match.group(1).rstrip(_TRAILING_PUNCT).lower()
    # bioRxiv/medRxiv append a version suffix that must not split duplicates.
    doi = re.sub(r"v\d+$", "", doi)
    return doi or None


def extract_doi(*candidates: str | None) -> str | None:
    """Return the first parseable DOI across several candidate strings."""
    for candidate in candidates:
        doi = norm_doi(candidate)
        if doi:
            return doi
    return None


def extract_pmid(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        match = PMID_RE.search(candidate)
        if match:
            return match.group(1)
    return None


def norm_title(value: str | None) -> str:
    """Aggressively normalise a title so cosmetic differences collapse.

    Publishers vary in casing, entity encoding, trailing periods and markup
    (``<i>Candida auris</i>``), so we reduce to lowercase alphanumeric words.
    """
    text = strip_html(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _WS_RE.sub(" ", text).strip()


def block_key(title_norm: str) -> str:
    """Cheap blocking key for near-duplicate candidate generation.

    Uses the three longest words, sorted, so word-order and prefix differences
    ("Efficacy of X" vs "X: efficacy") still land in the same bucket.
    """
    words = [w for w in title_norm.split() if len(w) >= 4]
    if not words:
        return title_norm[:16]
    longest = sorted(sorted(words, key=len, reverse=True)[:3])
    return " ".join(longest)


def similarity(a: str, b: str) -> float:
    """Token-level Jaccard similarity — order independent and fast."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def parse_date(value) -> date | None:
    """Best-effort date parsing across the formats our sources emit."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # feedparser's struct_time
    if hasattr(value, "tm_year"):
        try:
            return date(value.tm_year, value.tm_mon, value.tm_mday)
        except ValueError:
            return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%Y %b %d",
        "%Y",
    ):
        try:
            return datetime.strptime(text[: len(fmt) + 6], fmt).date()
        except ValueError:
            continue
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def today() -> date:
    return datetime.now(timezone.utc).date()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def truncate(text: str, limit: int) -> str:
    """Trim to a word boundary near ``limit`` characters."""
    if not text or len(text) <= limit:
        return text or ""
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(_TRAILING_PUNCT + " ") + "…"
