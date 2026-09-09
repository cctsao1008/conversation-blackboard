from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from blackboard_db import connect
from identity import register_identity, resolve_identity

HOST = os.environ.get("BLACKBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("BLACKBOARD_PORT", "8766"))
DB_PATH = os.environ.get("BLACKBOARD_DB", "board.db")


def _bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, sep, token = header.partition(" ")
    if sep != " " or scheme.lower() != "bearer" or not token:
        return None
    return token


class Handler(BaseHTTPRequestHandler):
    server_version = "conversation-blackboard/0"

    def _json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None

        if length <= 0 or length > 64 * 1024:
            return None

        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

        return value if isinstance(value, dict) else None

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/api/whoami":
            self._json(404, {"error": "not_found"})
            return

        token = _bearer_token(self.headers.get("Authorization"))
        if token is None:
            self._json(401, {"error": "unauthorized"})
            return

        conn = connect(DB_PATH)
        try:
            identity = resolve_identity(conn, token)
        finally:
            conn.close()

        if identity is None:
            self._json(401, {"error": "unauthorized"})
            return

        self._json(
            200,
            {
                "source": identity.source,
                "instance": identity.instance,
                "label": identity.label,
            },
        )

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/register":
            self._json(404, {"error": "not_found"})
            return

        registration_key = os.environ.get("BLACKBOARD_REGISTRATION_KEY")
        supplied_key = self.headers.get("X-Registration-Key")
        if not registration_key or supplied_key != registration_key:
            self._json(401, {"error": "unauthorized"})
            return

        body = self._read_json()
        if body is None:
            self._json(400, {"error": "invalid_json"})
            return

        source = body.get("source")
        label = body.get("label")
        if not isinstance(source, str):
            self._json(400, {"error": "invalid_source"})
            return
        if label is not None and not isinstance(label, str):
            self._json(400, {"error": "invalid_label"})
            return

        conn = connect(DB_PATH)
        try:
            try:
                identity, token = register_identity(conn, source, label=label)
            except ValueError as exc:
                self._json(400, {"error": "invalid_source", "detail": str(exc)})
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


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"conversation-blackboard listening on http://{HOST}:{PORT}")
    print(f"database: {DB_PATH}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
