#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _integrity_ok(conn: sqlite3.Connection) -> bool:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return bool(row and row[0] == "ok")


def backup_database(source_path: str | Path, backup_path: str | Path) -> None:
    source_path = Path(source_path)
    backup_path = Path(backup_path)

    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = backup_path.with_name(backup_path.name + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    source = sqlite3.connect(source_path, timeout=5.0)
    dest = sqlite3.connect(tmp_path)
    try:
        source.execute("PRAGMA busy_timeout = 5000")
        source.backup(dest)
        dest.commit()
        if not _integrity_ok(dest):
            raise RuntimeError("backup integrity check failed")
    finally:
        dest.close()
        source.close()

    tmp_path.replace(backup_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a consistent SQLite backup of a running conversation-blackboard database."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    backup_database(args.db, args.out)
    print(f"Backup created: {args.out}")


if __name__ == "__main__":
    main()
