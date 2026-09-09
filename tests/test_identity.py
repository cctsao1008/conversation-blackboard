from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from blackboard_db import append_message, connect, initialize, list_channels, list_messages_after
from identity import hash_token, register_identity, resolve_identity, revoke_token, rotate_token


class CoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "board.db"
        initialize(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schema_and_persistence(self) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO messages (channel, source, instance, kind, body)
                VALUES ('general', 'test-project', 'i-test', 'message', 'hello')
                """
            )
            conn.commit()
        finally:
            conn.close()

        conn = connect(self.db_path)
        try:
            rows = list_messages_after(conn, 0)
            self.assertEqual(1, len(rows))
            self.assertEqual("hello", rows[0]["body"])
        finally:
            conn.close()

    def test_channel_and_cursor_queries(self) -> None:
        conn = connect(self.db_path)
        try:
            conn.executemany(
                """
                INSERT INTO messages (channel, source, instance, kind, body)
                VALUES (?, 'test-project', 'i-test', 'message', ?)
                """,
                [
                    ("alpha", "a1"),
                    ("beta", "b1"),
                    ("alpha", "a2"),
                ],
            )
            conn.commit()

            rows = list_messages_after(conn, 1)
            self.assertEqual([2, 3], [row["id"] for row in rows])

            alpha = list_messages_after(conn, 0, channel="alpha")
            self.assertEqual([1, 3], [row["id"] for row in alpha])

            channels = list_channels(conn)
            self.assertEqual("alpha", channels[0]["channel"])
            self.assertEqual(2, channels[0]["message_count"])
            self.assertEqual(3, channels[0]["last_id"])
        finally:
            conn.close()

    def test_register_resolve_rotate_and_revoke(self) -> None:
        conn = connect(self.db_path)
        try:
            identity, token = register_identity(
                conn,
                "rotary-inverted-pendulum",
                label="control architecture",
            )
            self.assertTrue(identity.instance.startswith("i-"))

            resolved = resolve_identity(conn, token)
            self.assertEqual(identity, resolved)
            self.assertIsNone(resolve_identity(conn, "definitely-not-valid"))

            stored = conn.execute(
                "SELECT token_hash FROM identities WHERE instance = ?",
                (identity.instance,),
            ).fetchone()["token_hash"]
            self.assertEqual(hash_token(token), stored)
            self.assertNotEqual(token, stored)

            new_token = rotate_token(conn, identity.instance)
            self.assertIsNone(resolve_identity(conn, token))
            self.assertEqual(identity, resolve_identity(conn, new_token))

            revoke_token(conn, identity.instance)
            self.assertIsNone(resolve_identity(conn, new_token))
            self.assertIsNone(
                conn.execute(
                    "SELECT token_hash FROM identities WHERE instance = ?",
                    (identity.instance,),
                ).fetchone()["token_hash"]
            )
        finally:
            conn.close()

    def test_append_uses_resolved_identity(self) -> None:
        conn = connect(self.db_path)
        try:
            identity, _ = register_identity(conn, "single-wheel-platform")
            row = append_message(
                conn,
                identity,
                channel="control-systems",
                kind="insight",
                body="identity provenance test",
            )
            self.assertEqual(identity.source, row["source"])
            self.assertEqual(identity.instance, row["instance"])
        finally:
            conn.close()

    def test_token_hash_is_unique(self) -> None:
        conn = connect(self.db_path)
        try:
            token_hash = hash_token("same-token")
            conn.execute(
                """
                INSERT INTO identities(instance, source, token_hash)
                VALUES ('i-a', 'a', ?)
                """,
                (token_hash,),
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO identities(instance, source, token_hash)
                    VALUES ('i-b', 'b', ?)
                    """,
                    (token_hash,),
                )
                conn.commit()
        finally:
            conn.close()

    def test_invalid_source_is_rejected(self) -> None:
        conn = connect(self.db_path)
        try:
            with self.assertRaises(ValueError):
                register_identity(conn, "not valid source !")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
