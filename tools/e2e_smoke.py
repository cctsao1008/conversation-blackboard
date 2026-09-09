#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(base: str, method: str, path: str, *, token=None, key=None, payload=None):
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["X-Registration-Key"] = key
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = Request(base + path, data=data, headers=headers, method=method)
    with urlopen(request, timeout=2) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _start_server(db_path: Path, port: int, registration_key: str):
    env = os.environ.copy()
    env["BLACKBOARD_DB"] = str(db_path)
    env["BLACKBOARD_HOST"] = "127.0.0.1"
    env["BLACKBOARD_PORT"] = str(port)
    env["BLACKBOARD_REGISTRATION_KEY"] = registration_key
    return subprocess.Popen(
        [sys.executable, str(ROOT / "server.py")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_ready(base: str, process: subprocess.Popen) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("server exited before becoming ready")
        try:
            # 401 proves the HTTP server is alive without requiring a token.
            _request(base, "GET", "/api/whoami")
        except HTTPError as exc:
            if exc.code == 401:
                return
        except URLError:
            pass
        time.sleep(0.05)
    raise RuntimeError("server did not become ready")


def _stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "board.db"
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        registration_key = "e2e-local-only"

        server = _start_server(db_path, port, registration_key)
        try:
            _wait_ready(base, server)

            _, a = _request(
                base,
                "POST",
                "/api/register",
                key=registration_key,
                payload={"source": "project-a", "label": "conversation A"},
            )
            _, b = _request(
                base,
                "POST",
                "/api/register",
                key=registration_key,
                payload={"source": "project-b", "label": "conversation B"},
            )

            _, who_a = _request(base, "GET", "/api/whoami", token=a["token"])
            _, who_b = _request(base, "GET", "/api/whoami", token=b["token"])
            assert who_a["instance"] != who_b["instance"]

            _, first = _request(
                base,
                "POST",
                "/api/messages",
                token=a["token"],
                payload={
                    "channel": "e2e",
                    "kind": "message",
                    "body": "hello from A — UTF-8 測試",
                },
            )
            first_id = first["message"]["id"]

            _, seen = _request(
                base,
                "GET",
                f"/api/messages?after=0&channel=e2e",
                token=b["token"],
            )
            assert [m["id"] for m in seen["messages"]] == [first_id]

            _, second = _request(
                base,
                "POST",
                "/api/messages",
                token=b["token"],
                payload={
                    "channel": "e2e",
                    "kind": "reply",
                    "body": "reply from B",
                    "reply_to": first_id,
                },
            )
            second_id = second["message"]["id"]
            assert second_id == first_id + 1
        finally:
            _stop(server)

        # Restart the backend against the same DB and verify persistence.
        server = _start_server(db_path, port, registration_key)
        try:
            _wait_ready(base, server)
            _, persisted = _request(
                base,
                "GET",
                "/api/messages?after=0&channel=e2e",
                token=a["token"],
            )
            assert [m["id"] for m in persisted["messages"]] == [first_id, second_id]
        finally:
            _stop(server)

        # Direct DB verification proves server-resolved provenance and reply storage.
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT id, source, instance, channel, body, reply_to
                FROM messages
                ORDER BY id
                """
            ).fetchall()
        finally:
            conn.close()

        assert rows[0][1] == "project-a"
        assert rows[0][2] == who_a["instance"]
        assert rows[1][1] == "project-b"
        assert rows[1][2] == who_b["instance"]
        assert rows[1][5] == first_id

        print("PASS: two identities exchanged persistent messages through the HTTP API")
        print(f"message ids: {first_id}, {second_id}")
        print("PASS: restart persistence, cursor/channel read, reply relation, UTF-8, provenance")


if __name__ == "__main__":
    main()
