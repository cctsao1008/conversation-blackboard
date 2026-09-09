#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackboard_db import initialize  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize a conversation-blackboard SQLite database.")
    parser.add_argument("--db", required=True)
    args = parser.parse_args()

    initialize(args.db)
    print(f"Initialized {args.db}")


if __name__ == "__main__":
    main()
