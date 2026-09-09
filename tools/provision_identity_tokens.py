#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackboard_db import connect  # noqa: E402
from identity import revoke_token, rotate_token  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rotate or revoke bearer tokens for existing blackboard identities."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--instance", action="append", required=True)
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        rows = {
            row["instance"]: row
            for row in conn.execute(
                "SELECT instance, source, label FROM identities"
            )
        }

        for instance in args.instance:
            if instance not in rows:
                raise SystemExit(f"Unknown instance: {instance}")

        if args.revoke:
            for instance in args.instance:
                revoke_token(conn, instance)
                print(f"revoked: {instance}")
            return

        issued = []
        for instance in args.instance:
            row = rows[instance]
            token = rotate_token(conn, instance)
            issued.append((instance, row["source"], row["label"], token))

        print()
        print("SAVE THESE TOKENS NOW. Only SHA-256 hashes are stored in the database.")
        print()
        for instance, source, label, token in issued:
            print(f"instance: {instance}")
            print(f"source  : {source}")
            print(f"label   : {label or ''}")
            print(f"token   : {token}")
            print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
