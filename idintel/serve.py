"""Local companion server.

Serves the generated reports over http://localhost and exposes a small API the
Archive buttons call. Runs on the host, so it can reach both the SQLite database
and (via the Zotero Web API) the user's library. Stdlib only.

Routes:
    GET  /                     redirect to the latest daily report
    GET  /reports/<path>       serve a file from out/
    GET  /api/status           health + Zotero configured?
    POST /api/archive          body {"id": <record_id>} → create Zotero item
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import util
from .config import Config
from .db import Database
from .zotero import Zotero, ZoteroConfig, ZoteroError


class Handler(BaseHTTPRequestHandler):
    cfg: Config = None            # injected by make_server
    _zotero: Zotero | None = None
    _zotero_error: str | None = None

    server_version = "idintel/0.1"

    # ------------------------------------------------------------- utilities

    def log_message(self, fmt, *args):  # quieter than the default
        pass

    def _cors(self) -> None:
        # Reports opened via file:// send Origin: null; allow the archive API to
        # be called from there as well as from the server-hosted pages.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _zot(self) -> Zotero:
        """Lazily build a shared Zotero client, caching config errors."""
        cls = type(self)
        if cls._zotero is None:
            zcfg = ZoteroConfig.from_settings(self.cfg.settings)  # raises ZoteroError
            cls._zotero = Zotero(zcfg)
        return cls._zotero

    # --------------------------------------------------------------- routing

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._serve_latest()
        if path == "/api/status":
            return self._status()
        if path.startswith("/reports/"):
            return self._serve_report(unquote(path[len("/reports/"):]))
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/archive":
            return self._archive()
        self._json(404, {"error": "not found"})

    # --------------------------------------------------------------- handlers

    def _serve_latest(self):
        latest = self.cfg.settings.out_dir / "latest-daily.html"
        if latest.exists():
            self.send_response(302)
            self.send_header("Location", "/reports/latest-daily.html")
            self.end_headers()
        else:
            self._json(404, {"error": "no daily report yet — run `idintel daily`"})

    def _serve_report(self, rel: str):
        out = self.cfg.settings.out_dir.resolve()
        target = (out / rel).resolve()
        # Path-traversal guard: never serve outside out/.
        if not str(target).startswith(str(out)) or not target.is_file():
            return self._json(404, {"error": "not found"})
        data = target.read_bytes()
        self.send_response(200)
        ctype = "text/html; charset=utf-8" if target.suffix == ".html" else "application/octet-stream"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _status(self):
        configured, detail = True, "configured"
        try:
            ZoteroConfig.from_settings(self.cfg.settings)
        except ZoteroError as exc:
            configured, detail = False, str(exc)
        self._json(200, {"ok": True, "zotero_configured": configured, "detail": detail})

    def _archive(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or "{}")
            record_id = int(payload["id"])
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "expected JSON body {\"id\": <int>}"})

        db = Database(self.cfg.settings.db_path)
        try:
            row = db.one("SELECT * FROM records WHERE id = ?", (record_id,))
            if not row:
                return self._json(404, {"error": f"no record {record_id}"})
            if row["archived_at"]:
                return self._json(200, {
                    "ok": True, "already": True,
                    "zotero_key": row["zotero_key"], "archived_at": row["archived_at"],
                })
            try:
                zot = self._zot()
                key = zot.archive_record(row)
            except ZoteroError as exc:
                return self._json(502, {"error": str(exc)})

            when = util.now_iso()
            with db.tx() as conn:
                conn.execute(
                    "UPDATE records SET archived_at = ?, zotero_key = ? WHERE id = ?",
                    (when, key, record_id),
                )
            return self._json(200, {"ok": True, "zotero_key": key, "archived_at": when})
        finally:
            db.close()


def make_server(cfg: Config, host: str, port: int) -> ThreadingHTTPServer:
    Handler.cfg = cfg
    # Reset any cached client between runs (config may have changed).
    Handler._zotero = None
    return ThreadingHTTPServer((host, port), Handler)


def serve(cfg: Config, host: str = "127.0.0.1", port: int = 8791):
    httpd = make_server(cfg, host, port)
    return httpd
