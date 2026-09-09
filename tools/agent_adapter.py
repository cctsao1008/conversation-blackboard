#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackboard_client import BlackboardClient, BlackboardError  # noqa: E402


def _client(args) -> BlackboardClient:
    base_url = args.url or os.environ.get("BLACKBOARD_URL", "http://127.0.0.1:8766")
    token = args.token or os.environ.get("BLACKBOARD_TOKEN")
    if not token:
        raise SystemExit("Set BLACKBOARD_TOKEN or pass --token. Do not place tokens in repository files.")
    return BlackboardClient(base_url, token)


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vendor-neutral CLI adapter for conversation-blackboard."
    )
    parser.add_argument("--url", help="Board base URL; defaults to BLACKBOARD_URL or localhost.")
    parser.add_argument("--token", help="Bearer token; prefer BLACKBOARD_TOKEN environment variable.")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("whoami")
    sub.add_parser("channels")

    read = sub.add_parser("read")
    read.add_argument("--after", type=int, default=0)
    read.add_argument("--channel")
    read.add_argument("--limit", type=int, default=100)

    post = sub.add_parser("post")
    post.add_argument("--channel", required=True)
    post.add_argument("--kind", default="message")
    post.add_argument("--body", required=True)
    post.add_argument("--reply-to", type=int)

    args = parser.parse_args()
    client = _client(args)

    try:
        if args.command == "whoami":
            identity = client.whoami()
            _print({
                "source": identity.source,
                "instance": identity.instance,
                "label": identity.label,
            })
        elif args.command == "channels":
            _print(client.channels())
        elif args.command == "read":
            _print(client.messages(after=args.after, channel=args.channel, limit=args.limit))
        elif args.command == "post":
            _print(client.post(
                channel=args.channel,
                kind=args.kind,
                body=args.body,
                reply_to=args.reply_to,
            ))
    except BlackboardError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
