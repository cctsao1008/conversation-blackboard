from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def initialize(db_path: str | Path, schema_path: str | Path | None = None) -> None:
    if schema_path is None:
        schema_path = Path(__file__).with_name("schema.sql")

    schema = Path(schema_path).read_text(encoding="utf-8")
    conn = connect(db_path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()


def list_channels(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT channel, COUNT(*) AS message_count, MAX(id) AS last_id
        FROM messages
        GROUP BY channel
        ORDER BY last_id DESC
        """
    ).fetchall()


def list_messages_after(
    conn: sqlite3.Connection,
    after_id: int,
    *,
    channel: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    if channel is None:
        return conn.execute(
            """
            SELECT id, created_at, channel, source, instance, kind, body, reply_to
            FROM messages
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (after_id, limit),
        ).fetchall()

    return conn.execute(
        """
        SELECT id, created_at, channel, source, instance, kind, body, reply_to
        FROM messages
        WHERE channel = ? AND id > ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (channel, after_id, limit),
    ).fetchall()
