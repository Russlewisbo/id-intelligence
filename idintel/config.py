"""Configuration loading.

All tunables live in ``config/*.yaml`` so the pipeline can be retuned without
touching Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _read(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing config file: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Settings:
    raw: dict[str, Any] = field(default_factory=dict)

    # --- paths
    @property
    def db_path(self) -> Path:
        return ROOT / self.raw.get("db_path", "data/idintel.db")

    @property
    def out_dir(self) -> Path:
        return ROOT / self.raw.get("out_dir", "out")

    @property
    def log_dir(self) -> Path:
        return ROOT / self.raw.get("log_dir", "logs")

    # --- collection
    @property
    def lookback_days(self) -> int:
        return int(self.raw.get("collect", {}).get("lookback_days", 3))

    @property
    def rss_lookback_days(self) -> int:
        return int(self.raw.get("collect", {}).get("rss_lookback_days", 45))

    @property
    def fetch_workers(self) -> int:
        return int(self.raw.get("collect", {}).get("workers", 12))

    @property
    def fetch_timeout(self) -> float:
        return float(self.raw.get("collect", {}).get("timeout_seconds", 30))

    @property
    def user_agent(self) -> str:
        return self.raw.get("collect", {}).get(
            "user_agent",
            "idintel/0.1 (personal literature surveillance; +mailto:russ.e.lewis@gmail.com)",
        )

    @property
    def pubmed(self) -> dict:
        return self.raw.get("pubmed", {}) or {}

    @property
    def preprints(self) -> dict:
        return self.raw.get("preprints", {}) or {}

    @property
    def trials(self) -> dict:
        return self.raw.get("trials", {}) or {}

    # --- dedup
    @property
    def dedupe_threshold(self) -> float:
        return float(self.raw.get("dedupe", {}).get("title_similarity", 0.90))

    @property
    def dedupe_window_days(self) -> int:
        return int(self.raw.get("dedupe", {}).get("window_days", 180))

    # --- summarisation
    @property
    def summary(self) -> dict:
        return self.raw.get("summary", {}) or {}

    @property
    def summary_min_score(self) -> float:
        return float(self.summary.get("min_score", 20))

    @property
    def summary_max_per_run(self) -> int:
        return int(self.summary.get("max_per_run", 40))

    @property
    def summary_workers(self) -> int:
        return int(self.summary.get("workers", 3))

    @property
    def summary_model(self) -> str:
        return self.summary.get("model", "sonnet")

    @property
    def summary_timeout(self) -> int:
        return int(self.summary.get("timeout_seconds", 300))

    # --- report
    @property
    def report(self) -> dict:
        return self.raw.get("report", {}) or {}


@dataclass
class Config:
    settings: Settings
    feeds: dict
    scoring: dict

    @classmethod
    def load(cls) -> "Config":
        return cls(
            settings=Settings(_read("settings.yaml")),
            feeds=_read("feeds.yaml"),
            scoring=_read("scoring.yaml"),
        )

    def enabled_feeds(self) -> list[dict]:
        """Flatten ``feeds.yaml`` groups into a list of feed dicts."""
        out: list[dict] = []
        for group_name, entries in (self.feeds.get("groups") or {}).items():
            for entry in entries or []:
                if entry.get("enabled") is False:
                    continue
                feed = dict(entry)
                feed.setdefault("group", group_name)
                feed.setdefault("kind", "article")
                feed.setdefault("name", feed.get("url", "unnamed"))
                out.append(feed)
        return out
