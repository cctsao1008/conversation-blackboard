from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from blackboard_db import append_message, connect, initialize
from identity import register_identity
from tools.backup_db import backup_database
from tools.restore_db import restore_database


class BackupTests(unittest.TestCase):
    def test_backup_and_restore_preserve_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "runtime" / "board.db"
            backup = root / "backup" / "board-backup.db"
            restored = root / "restored" / "board.db"
            db.parent.mkdir(parents=True)
            initialize(db)

            live = connect(db)
            identity, _ = register_identity(live, "project-a")
            row = append_message(
                live,
                identity,
                channel="general",
                kind="message",
                body="persist me",
            )
            token_hash = live.execute(
                "SELECT token_hash FROM identities WHERE instance = ?",
                (identity.instance,),
            ).fetchone()["token_hash"]

            # Keep a live connection open: sqlite3 backup() must still make a
            # consistent snapshot of the WAL-backed database.
            backup_database(db, backup)
            live.close()

            restore_database(backup, restored)

            conn = sqlite3.connect(restored)
            try:
                message = conn.execute("SELECT id, body FROM messages").fetchone()
                restored_hash = conn.execute(
                    "SELECT token_hash FROM identities WHERE instance = ?",
                    (identity.instance,),
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual((row["id"], "persist me"), message)
            self.assertEqual(token_hash, restored_hash)


if __name__ == "__main__":
    unittest.main()
