"""Zotero integration.

Writes go through the Zotero **Web API** (pyzotero), not the local API: Zotero's
local HTTP API is read-only, so the local desktop client cannot be written to
directly. Items created via the Web API appear in the desktop library on the
next sync (near-instant when sync is enabled).

The API key lives in ``config/settings.yaml`` (git-ignored) and never leaves the
server process — the browser only ever talks to the local companion server.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import util
from .db import loads

COLLECTION_CACHE_KEY = "_idintel_collection_key"


class ZoteroError(RuntimeError):
    pass


@dataclass
class ZoteroConfig:
    api_key: str
    library_id: str
    library_type: str = "user"
    collection: str = "ID Intelligence"
    tag: str = "idintel"

    @classmethod
    def from_settings(cls, settings) -> "ZoteroConfig":
        z = settings.raw.get("zotero", {}) or {}
        api_key = (z.get("api_key") or "").strip()
        library_id = str(z.get("library_id") or "").strip()
        if not api_key or not library_id:
            raise ZoteroError(
                "Zotero is not configured. Set zotero.api_key and zotero.library_id "
                "in config/settings.yaml (see `idintel zotero-check`)."
            )
        return cls(
            api_key=api_key,
            library_id=library_id,
            library_type=z.get("library_type", "user"),
            collection=z.get("collection", "ID Intelligence"),
            tag=z.get("tag", "idintel"),
        )


def _client(cfg: ZoteroConfig):
    try:
        from pyzotero import zotero
    except ImportError as exc:  # pragma: no cover
        raise ZoteroError("pyzotero is not installed (pip install pyzotero)") from exc
    return zotero.Zotero(cfg.library_id, cfg.library_type, cfg.api_key)


class Zotero:
    """Thin wrapper: ensures the target collection exists and creates items."""

    def __init__(self, cfg: ZoteroConfig):
        self.cfg = cfg
        self.zot = _client(cfg)
        self._collection_key: str | None = None

    # ------------------------------------------------------------- collection

    def collection_key(self) -> str:
        """Return the target collection's key, creating it once if needed."""
        if self._collection_key:
            return self._collection_key
        try:
            for coll in self.zot.collections():
                if coll["data"]["name"] == self.cfg.collection and not coll["data"].get("parentCollection"):
                    self._collection_key = coll["key"]
                    return self._collection_key
            created = self.zot.create_collections([{"name": self.cfg.collection}])
            key = created["successful"]["0"]["key"]
            self._collection_key = key
            return key
        except ZoteroError:
            raise
        except Exception as exc:
            raise ZoteroError(f"could not resolve collection: {exc}") from exc

    # ------------------------------------------------------------------ items

    def item_from_record(self, row) -> dict:
        """Map a database row to a Zotero journalArticle item.

        Everything is filed as journalArticle — the record's true nature
        (preprint, trial) is preserved in the journal name and abstract, which
        avoids per-item-type field-validation pitfalls.
        """
        authors = loads(row["authors"], [])
        creators = [{"creatorType": "author", "name": a} for a in authors] or [
            {"creatorType": "author", "name": "[No author listed]"}
        ]

        sources = loads(row["sources"], [])
        extra_lines = []
        if row["nct"]:
            extra_lines.append(f"NCT: {row['nct']}")
        if row["score"] is not None:
            extra_lines.append(f"idintel score: {round(row['score'], 1)} ({row['priority']})")
        if sources:
            extra_lines.append("Source: " + "; ".join(sources))

        tags = [{"tag": self.cfg.tag}]
        summary = loads(row["summary"], None)
        if summary:
            for topic in (summary.get("topics") or [])[:5]:
                if topic:
                    tags.append({"tag": topic})

        url = row["url"] or (f"https://doi.org/{row['doi']}" if row["doi"] else "")

        item = {
            "itemType": "journalArticle",
            "title": row["title"] or "[untitled]",
            "creators": creators,
            "publicationTitle": row["journal"] or "",
            "date": row["published"] or "",
            "DOI": row["doi"] or "",
            "url": url,
            "abstractNote": row["abstract"] or "",
            "extra": "\n".join(extra_lines),
            "tags": tags,
            "accessDate": util.today().isoformat(),
        }
        if row["pmid"]:
            item["PMID"] = row["pmid"]
        return item

    def archive_record(self, row) -> str:
        """Create the Zotero item for a record and return its item key."""
        item = self.item_from_record(row)
        item["collections"] = [self.collection_key()]
        try:
            result = self.zot.create_items([item])
        except Exception as exc:
            raise ZoteroError(f"create_items failed: {exc}") from exc

        successful = result.get("successful") or {}
        if successful:
            return successful["0"]["key"]

        failed = result.get("failed") or {}
        if failed:
            msg = failed.get("0", {}).get("message", "unknown error")
            raise ZoteroError(f"Zotero rejected the item: {msg}")
        raise ZoteroError(f"unexpected Zotero response: {util.truncate(str(result), 200)}")

    def check(self) -> dict:
        """Round-trip diagnostic: verify key, library and collection access."""
        info = {"ok": False}
        try:
            # A cheap authenticated read confirms the key and library id.
            self.zot.items(limit=1)
            info["auth"] = True
            info["collection"] = self.cfg.collection
            info["collection_key"] = self.collection_key()
            info["ok"] = True
        except Exception as exc:
            info["error"] = str(exc)
        return info
