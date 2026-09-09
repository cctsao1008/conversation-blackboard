from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from blackboard_db import connect, initialize, list_messages_after
from identity import hash_token, register_identity, resolve_identity, rotate_token


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

    def test_register_resolve_and_rotate(self) -> None:
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

            stored = conn.execute(
                "SELECT token_hash FROM identities WHERE instance = ?",
                (identity.instance,),
            ).fetchone()["token_hash"]
            self.assertEqual(hash_token(token), stored)
            self.assertNotEqual(token, stored)

            new_token = rotate_token(conn, identity.instance)
            self.assertIsNone(resolve_identity(conn, token))
            self.assertEqual(identity, resolve_identity(conn, new_token))
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
