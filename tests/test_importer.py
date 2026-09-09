from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from blackboard_db import connect, initialize

ROOT = Path(__file__).resolve().parents[1]


def _make_docx(path: Path) -> None:
    xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>[single | 2026-09-08 00:01 +08:00] hello single</w:t></w:r></w:p>
<w:p><w:r><w:t>[rotary | 2026-09-08 00:10 +08:00] hello rotary</w:t></w:r></w:p>
</w:body></w:document>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


class ImporterTests(unittest.TestCase):
    def test_dry_run_import_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "board.db"
            docx = root / "fixture.docx"
            initialize(db)
            _make_docx(docx)

            cmd = [
                sys.executable,
                str(ROOT / "tools" / "import_shared_note.py"),
                "--db",
                str(db),
                "--docx",
                str(docx),
            ]

            dry = subprocess.run(
                cmd + ["--dry-run"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Found 2 entries", dry.stdout)

            conn = connect(db)
            self.assertEqual(
                0,
                conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            )
            conn.close()

            first = subprocess.run(
                cmd,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            second = subprocess.run(
                cmd,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Inserted: 2", first.stdout)
            self.assertIn("Skipped as duplicates: 2", second.stdout)

            conn = connect(db)
            try:
                rows = conn.execute(
                    "SELECT source, instance, body FROM messages ORDER BY id"
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(
                ("single", "legacy-single", "hello single"),
                tuple(rows[0]),
            )
            self.assertEqual(
                ("rotary", "legacy-rotary", "hello rotary"),
                tuple(rows[1]),
            )


if __name__ == "__main__":
    unittest.main()
