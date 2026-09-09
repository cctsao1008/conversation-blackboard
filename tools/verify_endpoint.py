#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackboard_client import BlackboardClient, BlackboardError  # noqa: E402


def _health(base_url: str, timeout: float) -> dict:
    request = Request(
        base_url.rstrip("/") + "/api/health",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"health check returned HTTP {exc.code}") from None
    except URLError as exc:
        raise RuntimeError(f"health check failed: {exc.reason}") from None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a conversation-blackboard endpoint without printing the bearer token. "
            "Useful after enabling a Cloudflare Tunnel."
        )
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("BLACKBOARD_URL"),
        help="Board base URL; defaults to BLACKBOARD_URL.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("BLACKBOARD_TOKEN"),
        help="Bearer token; prefer BLACKBOARD_TOKEN instead of the command line.",
    )
    parser.add_argument("--expect-source")
    parser.add_argument("--expect-instance")
    parser.add_argument("--channel")
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    if not args.url:
        raise SystemExit("Missing --url or BLACKBOARD_URL.")
    if not args.token:
        raise SystemExit("Missing --token or BLACKBOARD_TOKEN.")
    if args.after < 0:
        raise SystemExit("--after must be >= 0.")
    if not 1 <= args.limit <= 200:
        raise SystemExit("--limit must be between 1 and 200.")

    try:
        health = _health(args.url, args.timeout)
        if health.get("status") != "ok":
            raise RuntimeError(f"unexpected health response: {health!r}")

        client = BlackboardClient(args.url, args.token, timeout=args.timeout)
        identity = client.whoami()

        if args.expect_source is not None and identity.source != args.expect_source:
            raise RuntimeError(
                f"source mismatch: expected {args.expect_source!r}, got {identity.source!r}"
            )
        if args.expect_instance is not None and identity.instance != args.expect_instance:
            raise RuntimeError(
                f"instance mismatch: expected {args.expect_instance!r}, got {identity.instance!r}"
            )

        messages = client.messages(
            after=args.after,
            channel=args.channel,
            limit=args.limit,
        )

    except (BlackboardError, RuntimeError, URLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print("PASS: health")
    print(
        "PASS: whoami "
        f"source={identity.source} instance={identity.instance} "
        f"label={identity.label or ''}"
    )
    print(
        "PASS: messages "
        f"count={len(messages)} after={args.after} "
        f"channel={args.channel or '*'}"
    )
    if messages:
        print(f"latest_id={messages[-1]['id']}")


if __name__ == "__main__":
    main()
