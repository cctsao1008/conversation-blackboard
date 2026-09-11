#!/usr/bin/env python3
"""End-to-end UTCP and MCP convergence test for Conversation Blackboard.

The test starts one local Blackboard instance, provisions an independent REST
bearer identity plus an Ed25519 participant identity, discovers the provider
manual through /utcp, and proves that UTCP/native-HTTP and signed MCP access
paths observe the same canonical message log.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from utcp.data.utcp_client_config import UtcpClientConfig
from utcp.utcp_client import UtcpClient
from utcp_http.http_call_template import HttpCallTemplate

MCP_PROTOCOL_VERSION = "2025-11-25"
SIGNATURE_SCHEME = "ed25519-v1"
PRIVATE_KEY_PREFIX = "ed25519-sk:"
RING_PKCS8_V2_PREFIX = bytes(
    [
        0x30,
        0x51,
        0x02,
        0x01,
        0x01,
        0x30,
        0x05,
        0x06,
        0x03,
        0x2B,
        0x65,
        0x70,
        0x04,
        0x22,
        0x04,
        0x20,
    ]
)


def parse_field(output: str, field: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(field) and ":" in stripped:
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


def mcp_call(base_url: str, request_id: int, tool: str, arguments: dict) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    request = Request(
        f"{base_url}/mcp",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        },
    )
    with urlopen(request, timeout=5.0) as response:
        envelope = json.load(response)
    if "error" in envelope:
        raise RuntimeError(f"MCP JSON-RPC error: {envelope['error']}")
    result = envelope["result"]
    if result.get("isError"):
        raise RuntimeError(f"MCP tool error: {result}")
    return result["structuredContent"]


def base64url_decode_unpadded(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)


def base64url_encode_unpadded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def ring_private_seed(private_key: str) -> bytes:
    if not private_key.startswith(PRIVATE_KEY_PREFIX):
        raise RuntimeError("unexpected Ed25519 private-key prefix")
    der = base64url_decode_unpadded(private_key.removeprefix(PRIVATE_KEY_PREFIX))
    if len(der) != 83 or der[:16] != RING_PKCS8_V2_PREFIX:
        raise RuntimeError("unexpected ring Ed25519 PKCS#8 shape")
    return der[16:48]


def sign_write(
    private_key: str,
    participant_id: str,
    channel: str,
    kind: str,
    body: str,
    reply_to: int | None,
    nonce: str,
) -> str:
    key = Ed25519PrivateKey.from_private_bytes(ring_private_seed(private_key))
    payload = {
        "body": body,
        "channel": channel,
        "kind": kind,
        "nonce": nonce,
        "participant_id": participant_id,
        "reply_to": reply_to,
        "signature_version": SIGNATURE_SCHEME,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = key.sign(canonical)
    if len(signature) != 64:
        raise RuntimeError("unexpected Ed25519 signature length")
    return base64url_encode_unpadded(signature)


def sign_read(
    private_key: str,
    participant_id: str,
    channel: str,
    after: int,
    limit: int,
) -> str:
    key = Ed25519PrivateKey.from_private_bytes(ring_private_seed(private_key))
    payload = {
        "after": after,
        "channel": channel,
        "limit": limit,
        "participant_id": participant_id,
        "purpose": "blackboard-read-v1",
        "signature_version": SIGNATURE_SCHEME,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = key.sign(canonical)
    if len(signature) != 64:
        raise RuntimeError("unexpected Ed25519 signature length")
    return base64url_encode_unpadded(signature)


async def exercise_interfaces(
    base_url: str,
    bearer: str,
    rest_instance: str,
    participant_id: str,
    private_key: str,
) -> None:
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

    # UTCP -> native HTTP write using the independent REST bearer identity.
    utcp_posted = await client.call_tool(
        "blackboard.post_message",
        {
            "message": {
                "channel": "utcp-ci",
                "kind": "test",
                "body": "hello from UTCP",
            }
        },
    )
    utcp_message = utcp_posted["message"]
    assert utcp_message["source"] == "utcp-ci", utcp_message
    assert utcp_message["instance"] == rest_instance, utcp_message
    assert utcp_message["channel"] == "utcp-ci", utcp_message
    assert utcp_message["kind"] == "test", utcp_message
    assert utcp_message["body"] == "hello from UTCP", utcp_message

    # The independent MCP adapter must observe the exact same persisted message.
    read_signature = sign_read(
        private_key, participant_id, "utcp-ci", 0, 20
    )
    mcp_observed = mcp_call(
        base_url,
        1,
        "blackboard_read",
        {
            "participant_id": participant_id,
            "auth": {"scheme": SIGNATURE_SCHEME, "signature": read_signature},
            "channel": "utcp-ci",
            "after": 0,
            "limit": 20,
        },
    )
    assert mcp_observed["count"] == 1, mcp_observed
    assert mcp_observed["messages"][0]["id"] == utcp_message["id"], mcp_observed
    assert mcp_observed["messages"][0]["source"] == "utcp-ci", mcp_observed
    assert mcp_observed["messages"][0]["instance"] == rest_instance, mcp_observed

    # MCP -> Blackboard using an Ed25519 participant signature. The private key
    # remains local to this test client and is not part of the MCP arguments.
    channel = "utcp-ci"
    kind = "test"
    body = "hello from MCP"
    nonce = "utcp-mcp-convergence-001"
    signature = sign_write(
        private_key,
        participant_id,
        channel,
        kind,
        body,
        None,
        nonce,
    )
    mcp_written = mcp_call(
        base_url,
        2,
        "blackboard_write",
        {
            "participant_id": participant_id,
            "auth": {"scheme": SIGNATURE_SCHEME, "signature": signature},
            "channel": channel,
            "kind": kind,
            "body": body,
            "reply_to": None,
            "nonce": nonce,
        },
    )
    assert mcp_written["source"] == "mcp-ci", mcp_written
    assert mcp_written["participant_id"] == participant_id, mcp_written
    assert mcp_written["instance"] == participant_id, mcp_written

    # UTCP/native HTTP must now observe both messages in canonical ID order.
    utcp_observed = await client.call_tool(
        "blackboard.read_messages",
        {"after": 0, "channel": "utcp-ci", "limit": 20},
    )
    messages = utcp_observed["messages"]
    assert len(messages) == 2, utcp_observed
    assert [row["id"] for row in messages] == sorted(row["id"] for row in messages), messages
    assert messages[0]["id"] == utcp_message["id"], messages
    assert messages[0]["body"] == "hello from UTCP", messages
    assert messages[1]["id"] == mcp_written["id"], messages
    assert messages[1]["source"] == "mcp-ci", messages
    assert messages[1]["instance"] == participant_id, messages
    assert messages[1]["body"] == body, messages


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

        rest_provision = subprocess.run(
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
        bearer = parse_field(rest_provision, "token")
        rest_instance = parse_field(rest_provision, "instance")

        participant_provision = subprocess.run(
            [
                str(binary),
                "participant",
                "provision",
                "--db",
                str(db_path),
                "--participant-id",
                "mcp-ci-main",
                "--source",
                "mcp-ci",
                "--label",
                "MCP convergence smoke",
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        participant_id = parse_field(participant_provision, "participant_id")

        signing_material = subprocess.run(
            [str(binary), "participant", "generate-signing-key"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        public_key = parse_field(signing_material, "public_key")
        private_key = parse_field(signing_material, "private_key")
        subprocess.run(
            [
                str(binary),
                "participant",
                "set-signing-key",
                "--db",
                str(db_path),
                "--participant-id",
                participant_id,
                "--public-key",
                public_key,
            ],
            check=True,
            text=True,
        )

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
            asyncio.run(
                exercise_interfaces(
                    base_url,
                    bearer,
                    rest_instance,
                    participant_id,
                    private_key,
                )
            )
            print("UTCP discovery + signed MCP convergence: PASS")
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
