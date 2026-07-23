"""The common in-flight representation every collector produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import util


@dataclass
class Record:
    title: str
    source: str                       # human-readable origin, e.g. "RSS: CID"
    kind: str = "article"             # article | preprint | trial | regulatory | guideline
    doi: str | None = None
    pmid: str | None = None
    nct: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    url: str | None = None
    published: date | None = None
    pub_types: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.title = util.strip_html(self.title)
        self.abstract = util.strip_html(self.abstract) or None
        self.doi = util.norm_doi(self.doi)
        if self.journal:
            self.journal = util.strip_html(self.journal)

    @property
    def title_norm(self) -> str:
        return util.norm_title(self.title)

    @property
    def block_key(self) -> str:
        return util.block_key(self.title_norm)

    def is_usable(self) -> bool:
        """Reject entries with no meaningful title (feed separators, adverts)."""
        return len(self.title_norm.split()) >= 3
