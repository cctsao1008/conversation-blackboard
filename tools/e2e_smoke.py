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
sys.path.insert(0, str(ROOT))

from blackboard_client import BlackboardClient  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(base: str, method: str, path: str, *, token=None, key=None, payload=None, raw=None):
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["X-Registration-Key"] = key
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    elif raw is not None:
        headers["Content-Type"] = "application/json"
        data = raw

    request = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _text(base: str, path: str) -> tuple[int, str, dict[str, str]]:
    with urlopen(base + path, timeout=2) as response:
        headers = {key.lower(): value for key, value in response.headers.items()}
        return response.status, response.read().decode("utf-8"), headers


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
            status, payload = _request(base, "GET", "/api/health")
            if status == 200 and payload.get("status") == "ok":
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

            status, html, headers = _text(base, "/")
            assert status == 200
            assert "conversation-blackboard" in html
            assert '<script src="/app.js" defer></script>' in html
            assert "content-security-policy" in headers

            status, script, _ = _text(base, "/app.js")
            assert status == 200 and "textContent" in script
            status, css, _ = _text(base, "/style.css")
            assert status == 200 and "@media" in css

            status, error = _request(base, "GET", "/api/messages")
            assert status == 401 and error == {"error": "unauthorized"}

            status, invalid_error = _request(
                base,
                "GET",
                "/api/messages",
                token="invalid-token",
            )
            assert status == 401 and invalid_error == error

            status, malformed = _request(
                base,
                "POST",
                "/api/register",
                key=registration_key,
                raw=b"{broken-json",
            )
            assert status == 400 and malformed["error"] == "invalid_json"

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

            status, spoof = _request(
                base,
                "POST",
                "/api/messages",
                token=a["token"],
                payload={
                    "channel": "e2e",
                    "body": "spoof attempt",
                    "source": "project-b",
                },
            )
            assert status == 400 and spoof["error"] == "identity_is_server_resolved"

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
                "/api/messages?after=0&channel=e2e",
                token=b["token"],
            )
            assert [m["id"] for m in seen["messages"]] == [first_id]
            assert seen["messages"][0]["body"].endswith("UTF-8 測試")

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

            _, cursor = _request(
                base,
                "GET",
                f"/api/messages?after={first_id}",
                token=a["token"],
            )
            assert [m["id"] for m in cursor["messages"]] == [second_id]

            client_a = BlackboardClient(base, a["token"])
            client_b = BlackboardClient(base, b["token"])
            assert client_a.whoami().instance == who_a["instance"]
            assert client_b.whoami().instance == who_b["instance"]

            adapter_first = client_a.post(
                channel="adapter-e2e",
                kind="insight",
                body="adapter message from A",
            )
            adapter_seen = client_b.messages(
                after=0,
                channel="adapter-e2e",
            )
            assert [m["id"] for m in adapter_seen] == [adapter_first["id"]]

            adapter_reply = client_b.post(
                channel="adapter-e2e",
                body="adapter reply from B",
                reply_to=adapter_first["id"],
            )
            assert adapter_reply["source"] == "project-b"
            assert adapter_reply["instance"] == who_b["instance"]

            verify_env = os.environ.copy()
            verify_env["BLACKBOARD_URL"] = base
            verify_env["BLACKBOARD_TOKEN"] = a["token"]
            verifier = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "verify_endpoint.py"),
                    "--expect-source",
                    who_a["source"],
                    "--expect-instance",
                    who_a["instance"],
                    "--channel",
                    "e2e",
                    "--after",
                    "0",
                ],
                cwd=ROOT,
                env=verify_env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert verifier.returncode == 0, verifier.stderr
            assert "PASS: health" in verifier.stdout
            assert "PASS: whoami" in verifier.stdout
            assert "PASS: messages" in verifier.stdout
            assert a["token"] not in verifier.stdout
            assert a["token"] not in verifier.stderr
        finally:
            _stop(server)

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

        print("PASS: browser assets/security headers + health/auth/messages/cursor/reply/UTF-8/provenance/restart")
        print("PASS: vendor-neutral adapter with two concurrent identities")
        print("PASS: endpoint verifier keeps credentials out of output")
        print(f"message ids: {first_id}, {second_id}")


if __name__ == "__main__":
    main()
