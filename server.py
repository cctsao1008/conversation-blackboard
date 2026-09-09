from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from blackboard_db import append_message, connect, initialize, list_channels, list_messages_after
from identity import Identity, register_identity, resolve_identity

HOST = os.environ.get("BLACKBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("BLACKBOARD_PORT", "8766"))
DB_PATH = os.environ.get("BLACKBOARD_DB", "board.db")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_KIND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
MAX_BODY_BYTES = 64 * 1024
MAX_PAGE_SIZE = 200


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
    server_version = "conversation-blackboard/0"

    def log_message(self, fmt: str, *args) -> None:
        # Keep normal request logs, but never include Authorization header values.
        super().log_message(fmt, *args)

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

        if length <= 0 or length > MAX_BODY_BYTES:
            return None

        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

        return value if isinstance(value, dict) else None

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
        parsed = urlparse(self.path)
        path = parsed.path

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
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/register":
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
            return

        if path == "/api/messages":
            identity = self._require_identity()
            if identity is None:
                return

            body = self._read_json()
            if body is None:
                self._json(400, {"error": "invalid_json"})
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
            if reply_to is not None and (not isinstance(reply_to, int) or reply_to <= 0):
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
    print(f"database: {DB_PATH}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
