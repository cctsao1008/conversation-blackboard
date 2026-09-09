#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _integrity_ok(conn: sqlite3.Connection) -> bool:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return bool(row and row[0] == "ok")


def restore_database(
    backup_path: str | Path,
    target_path: str | Path,
    *,
    force: bool = False,
) -> None:
    backup_path = Path(backup_path)
    target_path = Path(target_path)

    if not backup_path.is_file():
        raise FileNotFoundError(backup_path)
    if target_path.exists() and not force:
        raise FileExistsError(f"target exists: {target_path}")

    source = sqlite3.connect(backup_path)
    try:
        if not _integrity_ok(source):
            raise RuntimeError("backup integrity check failed")
    finally:
        source.close()

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(target_path.name + ".restore-tmp")
    for path in (
        tmp_path,
        Path(str(tmp_path) + "-wal"),
        Path(str(tmp_path) + "-shm"),
    ):
        if path.exists():
            path.unlink()

    source = sqlite3.connect(backup_path)
    dest = sqlite3.connect(tmp_path)
    try:
        source.backup(dest)
        dest.commit()
        if not _integrity_ok(dest):
            raise RuntimeError("restored database integrity check failed")
    finally:
        dest.close()
        source.close()

    if force:
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(target_path) + suffix)
            if path.exists():
                path.unlink()

    tmp_path.replace(target_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore a conversation-blackboard SQLite backup. Stop the server before restoring."
    )
    parser.add_argument("--backup", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    restore_database(args.backup, args.db, force=args.force)
    print(f"Database restored: {args.db}")


if __name__ == "__main__":
    main()
