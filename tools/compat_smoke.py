#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(base: str, method: str, path: str, *, token=None, key=None, payload=None):
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["X-Registration-Key"] = key
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8")), response.headers
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")), exc.headers


def start(runtime: str, db: Path, port: int, key: str) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        BLACKBOARD_DB=str(db),
        BLACKBOARD_HOST="127.0.0.1",
        BLACKBOARD_PORT=str(port),
        BLACKBOARD_REGISTRATION_KEY=key,
    )
    if runtime == "python":
        command = [sys.executable, str(ROOT / "server.py")]
    else:
        binary = ROOT / "target" / "debug" / ("conversation-blackboard.exe" if os.name == "nt" else "conversation-blackboard")
        command = [str(binary)]
    return subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(base: str, process: subprocess.Popen) -> None:
    deadline = time.time() + 8
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("runtime exited before becoming ready")
        try:
            status, body, _ = request(base, "GET", "/api/health")
            if status == 200 and body == {"status": "ok"}:
                return
        except URLError:
            pass
        time.sleep(0.05)
    raise RuntimeError("runtime did not become ready")


def stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def insert_fixed_identity(db: Path) -> str:
    token = "compat-fixed-token"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = sqlite3.connect(db, timeout=5.0)
    try:
        conn.execute(
            "INSERT INTO identities (instance, source, label, token_hash) VALUES (?, ?, ?, ?)",
            ("compat-fixed-instance", "compat-fixed", "pre-existing token hash", token_hash),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def run(runtime: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "board.db"
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        key = "compat-local-key"
        process = start(runtime, db, port, key)
        try:
            wait_ready(base, process)

            fixed_token = insert_fixed_identity(db)
            status, fixed, _ = request(base, "GET", "/api/whoami", token=fixed_token)
            assert status == 200 and fixed == {
                "source": "compat-fixed",
                "instance": "compat-fixed-instance",
                "label": "pre-existing token hash",
            }

            status, body, headers = request(base, "GET", "/api/health")
            assert status == 200 and body == {"status": "ok"}
            assert headers.get("X-Content-Type-Options") == "nosniff"

            status, body, _ = request(base, "GET", "/api/messages")
            assert status == 401 and body == {"error": "unauthorized"}

            status, body, _ = request(base, "GET", "/api/messages", token="wrong")
            assert status == 401 and body == {"error": "unauthorized"}

            status, a, _ = request(
                base,
                "POST",
                "/api/register",
                key=key,
                payload={"source": "compat-a", "label": "conversation A"},
            )
            assert status == 201 and a["source"] == "compat-a" and a["instance"].startswith("i-")

            status, b, _ = request(
                base,
                "POST",
                "/api/register",
                key=key,
                payload={"source": "compat-b", "label": "conversation B"},
            )
            assert status == 201 and b["source"] == "compat-b" and a["instance"] != b["instance"]

            status, who, _ = request(base, "GET", "/api/whoami", token=a["token"])
            assert status == 200 and who == {
                "source": "compat-a",
                "instance": a["instance"],
                "label": "conversation A",
            }

            status, spoof, _ = request(
                base,
                "POST",
                "/api/messages",
                token=a["token"],
                payload={"channel": "compat", "body": "spoof", "source": "compat-b"},
            )
            assert status == 400 and spoof == {"error": "identity_is_server_resolved"}

            status, first, _ = request(
                base,
                "POST",
                "/api/messages",
                token=a["token"],
                payload={"channel": "compat", "kind": "insight", "body": "UTF-8 相容性"},
            )
            assert status == 201
            first = first["message"]
            assert first["source"] == "compat-a" and first["instance"] == a["instance"]

            status, seen, _ = request(
                base,
                "GET",
                f"/api/messages?after=0&channel=compat&limit=100",
                token=b["token"],
            )
            assert status == 200 and [row["id"] for row in seen["messages"]] == [first["id"]]

            status, second, _ = request(
                base,
                "POST",
                "/api/messages",
                token=b["token"],
                payload={"channel": "compat", "body": "reply", "reply_to": first["id"]},
            )
            assert status == 201
            second = second["message"]
            assert second["id"] == first["id"] + 1 and second["reply_to"] == first["id"]

            status, cursor, _ = request(
                base,
                "GET",
                f"/api/messages?after={first['id']}&channel=compat",
                token=a["token"],
            )
            assert status == 200 and [row["id"] for row in cursor["messages"]] == [second["id"]]

            status, channels, _ = request(base, "GET", "/api/channels", token=a["token"])
            assert status == 200 and channels["channels"][0] == {
                "channel": "compat",
                "message_count": 2,
                "last_id": second["id"],
            }

            status, invalid, _ = request(base, "GET", "/api/messages?after=-1", token=a["token"])
            assert status == 400 and invalid == {"error": "invalid_query"}
        finally:
            stop(process)

        process = start(runtime, db, port, key)
        try:
            wait_ready(base, process)
            status, persisted, _ = request(
                base,
                "GET",
                "/api/messages?after=0&channel=compat",
                token=a["token"],
            )
            assert status == 200 and len(persisted["messages"]) == 2
            status, fixed, _ = request(base, "GET", "/api/whoami", token=fixed_token)
            assert status == 200 and fixed["instance"] == "compat-fixed-instance"
        finally:
            stop(process)

    print(f"PASS: compatibility contract ({runtime})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=("python", "rust"), required=True)
    args = parser.parse_args()
    run(args.runtime)


if __name__ == "__main__":
    main()
