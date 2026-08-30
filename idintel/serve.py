"""Local companion server.

Serves the generated reports over http://localhost and exposes a small API the
Archive buttons call. Runs on the host, so it can reach both the SQLite database
and (via the Zotero Web API) the user's library. Stdlib only.

Routes:
    GET  /                     redirect to the latest daily report
    GET  /reports/<path>       serve a file from out/
    GET  /api/status           health + Zotero configured?
    POST /api/archive          body {"id": <record_id>} → create Zotero item

Cross-origin policy
-------------------
``/api/archive`` changes state (it writes to the user's Zotero library), so it
must not be callable by any website the user happens to have open. Two things
guard it, because either alone is insufficient:

* **Origin allow-list.** Only this server's own origins — and ``null``, which
  is what a report opened as ``file://`` sends — are echoed back in
  ``Access-Control-Allow-Origin``. A wildcard here would let any site read the
  responses, which leak record ids and Zotero item keys.
* **A required ``X-Idintel`` header.** CORS alone does not stop the request
  being *sent*: a "simple" cross-site POST (``text/plain`` body, no preflight)
  is delivered and acted upon before the browser ever checks the response
  headers. A custom header cannot be set cross-origin without a preflight that
  we refuse, so requiring one is what actually blocks the write.

Residual risk: a sandboxed iframe can also present ``Origin: null``. Supporting
``file://`` reports is a deliberate trade for that; drop ``"null"`` from
``ALLOWED_ORIGIN_EXTRA`` to close it, at the cost of the buttons only working
when the report is opened through this server.
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


# Reports loaded from disk rather than through the server send this Origin.
ALLOWED_ORIGIN_EXTRA = {"null"}

# Required on state-changing requests. The value is irrelevant; its presence is
# the point, since it forces a preflight the allow-list can refuse.
CSRF_HEADER = "X-Idintel"


class Handler(BaseHTTPRequestHandler):
    cfg: Config = None            # injected by make_server
    allowed_origins: set[str] = frozenset()   # injected by make_server
    _zotero: Zotero | None = None
    _zotero_error: str | None = None

    server_version = "idintel/0.1"

    # ------------------------------------------------------------- utilities

    def log_message(self, fmt, *args):  # quieter than the default
        pass

    def _origin_allowed(self, origin: str | None) -> bool:
        """No Origin at all (curl, same-origin GET) is fine; a foreign one is not."""
        return origin is None or origin in type(self).allowed_origins

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        # Echo the specific origin rather than "*": the responses carry record
        # ids and Zotero item keys, which no third-party page should read.
        if origin and origin in type(self).allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", f"Content-Type, {CSRF_HEADER}")
            self.send_header("Access-Control-Max-Age", "600")
        # Caches must not serve one origin's response to another.
        self.send_header("Vary", "Origin")

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
        # Refusing the preflight is what stops a foreign page from sending the
        # X-Idintel header that /api/archive requires.
        if not self._origin_allowed(self.headers.get("Origin")):
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.send_header("Vary", "Origin")
            self.end_headers()
            return
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
        # Path-traversal guard: never serve outside out/. A string prefix test
        # would also accept a sibling like "out-backup/"; is_relative_to does not.
        if not target.is_relative_to(out) or not target.is_file():
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
        origin = self.headers.get("Origin")
        if not self._origin_allowed(origin):
            return self._json(403, {"error": "cross-origin request refused"})
        # Blocks the no-preflight "simple request" path, which CORS does not.
        if not self.headers.get(CSRF_HEADER):
            return self._json(403, {"error": f"missing {CSRF_HEADER} header"})
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
    # Built from the actual bind port so a non-default --port still works.
    Handler.allowed_origins = frozenset(
        {f"http://{name}:{port}" for name in ("127.0.0.1", "localhost")}
        | ALLOWED_ORIGIN_EXTRA
    )
    # Reset any cached client between runs (config may have changed).
    Handler._zotero = None
    return ThreadingHTTPServer((host, port), Handler)


def serve(cfg: Config, host: str = "127.0.0.1", port: int = 8791):
    httpd = make_server(cfg, host, port)
    return httpd
