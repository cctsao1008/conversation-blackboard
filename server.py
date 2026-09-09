from __future__ import annotations

import hmac
import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from blackboard_db import append_message, connect, initialize, list_channels, list_messages_after
from identity import Identity, register_identity, resolve_identity

HOST = os.environ.get("BLACKBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("BLACKBOARD_PORT", "8766"))
DB_PATH = os.environ.get("BLACKBOARD_DB", "board.db")
WEB_ROOT = Path(__file__).with_name("web")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_KIND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
MAX_BODY_BYTES = 64 * 1024
MAX_PAGE_SIZE = 200

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def _bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, sep, token = header.partition(" ")
    if sep != " " or scheme.lower() != "bearer" or not token:
        return None
    return token


def _row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


class Handler(BaseHTTPRequestHandler):
    server_version = "conversation-blackboard"
    sys_version = ""

    def _common_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")

    def _json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self._common_security_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _static(self, path: str) -> bool:
        entry = _STATIC_FILES.get(path)
        if entry is None:
            return False

        filename, content_type = entry
        raw = (WEB_ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self._common_security_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        return True

    def _read_json(self) -> tuple[dict | None, str | None]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None, "invalid_json"

        if length <= 0:
            return None, "invalid_json"
        if length > MAX_BODY_BYTES:
            return None, "request_too_large"

        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "invalid_json"

        if not isinstance(value, dict):
            return None, "invalid_json"
        return value, None

    def _identity(self) -> Identity | None:
        token = _bearer_token(self.headers.get("Authorization"))
        if token is None:
            return None

        conn = connect(DB_PATH)
        try:
            return resolve_identity(conn, token)
        finally:
            conn.close()

    def _require_identity(self) -> Identity | None:
        identity = self._identity()
        if identity is None:
            self._json(401, {"error": "unauthorized"})
        return identity

    def do_GET(self) -> None:
        try:
            self._do_GET()
        except sqlite3.Error:
            self._json(503, {"error": "database_unavailable"})
        except (OSError, UnicodeError):
            self._json(500, {"error": "internal_error"})
        except Exception:
            self._json(500, {"error": "internal_error"})

    def _do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if self._static(path):
            return

        if path == "/api/health":
            conn = connect(DB_PATH)
            try:
                conn.execute("SELECT 1").fetchone()
            finally:
                conn.close()
            self._json(200, {"status": "ok"})
            return

        if path == "/api/whoami":
            identity = self._require_identity()
            if identity is None:
                return
            self._json(
                200,
                {
                    "source": identity.source,
                    "instance": identity.instance,
                    "label": identity.label,
                },
            )
            return

        if path == "/api/messages":
            identity = self._require_identity()
            if identity is None:
                return

            query = parse_qs(parsed.query)
            try:
                after = int(query.get("after", ["0"])[0])
                limit = int(query.get("limit", ["100"])[0])
            except ValueError:
                self._json(400, {"error": "invalid_query"})
                return

            if after < 0 or limit < 1 or limit > MAX_PAGE_SIZE:
                self._json(400, {"error": "invalid_query"})
                return

            channel = query.get("channel", [None])[0]
            if channel is not None and not _NAME_RE.fullmatch(channel):
                self._json(400, {"error": "invalid_channel"})
                return

            conn = connect(DB_PATH)
            try:
                rows = list_messages_after(conn, after, channel=channel, limit=limit)
            finally:
                conn.close()

            self._json(200, {"messages": [_row_dict(row) for row in rows]})
            return

        if path == "/api/channels":
            identity = self._require_identity()
            if identity is None:
                return

            conn = connect(DB_PATH)
            try:
                rows = list_channels(conn)
            finally:
                conn.close()

            self._json(200, {"channels": [_row_dict(row) for row in rows]})
            return

        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        try:
            self._do_POST()
        except sqlite3.Error:
            self._json(503, {"error": "database_unavailable"})
        except Exception:
            self._json(500, {"error": "internal_error"})

    def _do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/register":
            registration_key = os.environ.get("BLACKBOARD_REGISTRATION_KEY")
            supplied_key = self.headers.get("X-Registration-Key")
            if (
                not registration_key
                or supplied_key is None
                or not hmac.compare_digest(supplied_key, registration_key)
            ):
                self._json(401, {"error": "unauthorized"})
                return

            body, error = self._read_json()
            if error:
                self._json(413 if error == "request_too_large" else 400, {"error": error})
                return

            source = body.get("source")
            label = body.get("label")
            if not isinstance(source, str):
                self._json(400, {"error": "invalid_source"})
                return
            if label is not None and (not isinstance(label, str) or len(label) > 256):
                self._json(400, {"error": "invalid_label"})
                return

            conn = connect(DB_PATH)
            try:
                try:
                    identity, token = register_identity(conn, source, label=label)
                except ValueError:
                    self._json(400, {"error": "invalid_registration"})
                    return
            finally:
                conn.close()

            self._json(
                201,
                {
                    "source": identity.source,
                    "instance": identity.instance,
                    "label": identity.label,
                    "token": token,
                },
            )
            return

        if path == "/api/messages":
            identity = self._require_identity()
            if identity is None:
                return

            body, error = self._read_json()
            if error:
                self._json(413 if error == "request_too_large" else 400, {"error": error})
                return

            if "source" in body or "instance" in body:
                self._json(400, {"error": "identity_is_server_resolved"})
                return

            channel = body.get("channel")
            kind = body.get("kind", "message")
            message_body = body.get("body")
            reply_to = body.get("reply_to")

            if not isinstance(channel, str) or not _NAME_RE.fullmatch(channel):
                self._json(400, {"error": "invalid_channel"})
                return
            if not isinstance(kind, str) or not _KIND_RE.fullmatch(kind):
                self._json(400, {"error": "invalid_kind"})
                return
            if not isinstance(message_body, str) or not message_body.strip():
                self._json(400, {"error": "invalid_body"})
                return
            if len(message_body.encode("utf-8")) > MAX_BODY_BYTES:
                self._json(413, {"error": "request_too_large"})
                return
            if reply_to is not None and (
                isinstance(reply_to, bool)
                or not isinstance(reply_to, int)
                or reply_to <= 0
            ):
                self._json(400, {"error": "invalid_reply_to"})
                return

            conn = connect(DB_PATH)
            try:
                if reply_to is not None:
                    exists = conn.execute(
                        "SELECT 1 FROM messages WHERE id = ?",
                        (reply_to,),
                    ).fetchone()
                    if exists is None:
                        self._json(400, {"error": "reply_target_not_found"})
                        return

                row = append_message(
                    conn,
                    identity,
                    channel=channel,
                    kind=kind,
                    body=message_body,
                    reply_to=reply_to,
                )
            finally:
                conn.close()

            self._json(201, {"message": _row_dict(row)})
            return

        self._json(404, {"error": "not_found"})


def main() -> None:
    initialize(DB_PATH)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"conversation-blackboard listening on http://{HOST}:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
