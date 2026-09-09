from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass

_SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True)
class Identity:
    source: str
    instance: str
    label: str | None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_identity(conn: sqlite3.Connection, token: str) -> Identity | None:
    if not token:
        return None

    row = conn.execute(
        """
        SELECT source, instance, label
        FROM identities
        WHERE token_hash = ?
        LIMIT 1
        """,
        (hash_token(token),),
    ).fetchone()

    if row is None:
        return None

    return Identity(
        source=row["source"],
        instance=row["instance"],
        label=row["label"],
    )


def _validate_source(source: str) -> str:
    source = source.strip()
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError("invalid source")
    return source


def _new_instance_id() -> str:
    return f"i-{secrets.token_hex(6)}"


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def register_identity(
    conn: sqlite3.Connection,
    source: str,
    *,
    label: str | None = None,
) -> tuple[Identity, str]:
    source = _validate_source(source)
    label = label.strip() if isinstance(label, str) and label.strip() else None
    if label is not None and len(label) > 256:
        raise ValueError("invalid label")

    while True:
        instance = _new_instance_id()
        token = _new_token()
        try:
            conn.execute(
                """
                INSERT INTO identities (instance, source, label, token_hash)
                VALUES (?, ?, ?, ?)
                """,
                (instance, source, label, hash_token(token)),
            )
            conn.commit()
            return Identity(source=source, instance=instance, label=label), token
        except sqlite3.IntegrityError:
            conn.rollback()


def rotate_token(conn: sqlite3.Connection, instance: str) -> str:
    token = _new_token()
    cur = conn.execute(
        """
        UPDATE identities
        SET token_hash = ?
        WHERE instance = ?
        """,
        (hash_token(token), instance),
    )

    if cur.rowcount != 1:
        conn.rollback()
        raise KeyError(instance)

    conn.commit()
    return token


def revoke_token(conn: sqlite3.Connection, instance: str) -> None:
    cur = conn.execute(
        """
        UPDATE identities
        SET token_hash = NULL
        WHERE instance = ?
        """,
        (instance,),
    )

    if cur.rowcount != 1:
        conn.rollback()
        raise KeyError(instance)

    conn.commit()
