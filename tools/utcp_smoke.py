#!/usr/bin/env python3
"""End-to-end UTCP smoke test for Conversation Blackboard.

The test starts a local Blackboard instance, provisions one REST identity,
discovers the provider manual through /utcp, and invokes read_messages and
post_message through the official UTCP HTTP client.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from utcp.data.utcp_client_config import UtcpClientConfig
from utcp.utcp_client import UtcpClient
from utcp_http.http_call_template import HttpCallTemplate


def parse_field(output: str, field: str) -> str:
    prefix = f"{field}"
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix) and ":" in stripped:
            name, value = stripped.split(":", 1)
            if name.strip() == field and value.strip():
                return value.strip()
    raise RuntimeError(f"missing {field!r} in command output:\n{output}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(base_url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/api/health", timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # server may still be starting
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"Blackboard did not become healthy: {last_error}")


async def exercise_utcp(base_url: str, bearer: str, expected_instance: str) -> None:
    manual = HttpCallTemplate(
        name="blackboard",
        call_template_type="http",
        url=f"{base_url}/utcp",
        http_method="GET",
        content_type="application/json",
    )
    config = UtcpClientConfig(
        manual_call_templates=[manual],
        variables={
            "blackboard_BLACKBOARD_URL": base_url,
            "blackboard_BLACKBOARD_TOKEN": bearer,
        },
    )
    client = await UtcpClient.create(config=config)

    initial = await client.call_tool(
        "blackboard.read_messages",
        {"after": 0, "channel": "utcp-ci", "limit": 20},
    )
    assert initial == {"messages": []}, initial

    posted = await client.call_tool(
        "blackboard.post_message",
        {
            "message": {
                "channel": "utcp-ci",
                "kind": "test",
                "body": "hello from UTCP",
            }
        },
    )
    message = posted["message"]
    assert message["source"] == "utcp-ci", message
    assert message["instance"] == expected_instance, message
    assert message["channel"] == "utcp-ci", message
    assert message["kind"] == "test", message
    assert message["body"] == "hello from UTCP", message

    observed = await client.call_tool(
        "blackboard.read_messages",
        {"after": 0, "channel": "utcp-ci", "limit": 20},
    )
    messages = observed["messages"]
    assert len(messages) == 1, observed
    assert messages[0]["id"] == message["id"], observed
    assert messages[0]["source"] == "utcp-ci", observed
    assert messages[0]["instance"] == expected_instance, observed
    assert messages[0]["body"] == "hello from UTCP", observed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    args = parser.parse_args()

    binary = args.binary.resolve()
    if not binary.is_file():
        raise SystemExit(f"Blackboard binary not found: {binary}")

    with tempfile.TemporaryDirectory(prefix="blackboard-utcp-") as temp:
        db_path = Path(temp) / "board.db"
        subprocess.run(
            [str(binary), "db", "init", "--db", str(db_path)],
            check=True,
            text=True,
        )
        provision = subprocess.run(
            [
                str(binary),
                "identity",
                "provision",
                "--db",
                str(db_path),
                "--source",
                "utcp-ci",
                "--label",
                "UTCP integration smoke",
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        bearer = parse_field(provision, "token")
        instance = parse_field(provision, "instance")

        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        server = subprocess.Popen(
            [
                str(binary),
                "run",
                "--db",
                str(db_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_health(base_url)
            asyncio.run(exercise_utcp(base_url, bearer, instance))
            print("UTCP discovery/read/write smoke: PASS")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            if server.returncode not in (0, -15):
                output = server.stdout.read() if server.stdout else ""
                print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
