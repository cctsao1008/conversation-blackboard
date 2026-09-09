#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ENTRY_RE = re.compile(
    r"\[(single|rotary)\s*\|\s*"
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2} [+-]\d{2}:\d{2})"
    r"\]\s*",
    re.IGNORECASE,
)

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")

    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter(W_NS + "p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == W_NS + "t" and node.text:
                parts.append(node.text)
            elif node.tag == W_NS + "tab":
                parts.append("\t")
            elif node.tag in {W_NS + "br", W_NS + "cr"}:
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def read_entries(docx_path: Path):
    text = _docx_text(docx_path)
    matches = list(ENTRY_RE.finditer(text))
    entries = []

    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)

        source = match.group(1).lower()
        timestamp_text = match.group(2)
        body = text[body_start:body_end].strip()
        dt = datetime.strptime(timestamp_text, "%Y-%m-%d %H:%M %z")

        entries.append(
            {
                "source": source,
                "created_at": int(dt.timestamp()),
                "timestamp_text": timestamp_text,
                "body": body,
            }
        )

    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import the legacy Single/Rotary shared-note DOCX into conversation-blackboard."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--docx", required=True)
    parser.add_argument("--channel", default="control-systems")
    parser.add_argument("--single-instance", default="legacy-single")
    parser.add_argument("--rotary-instance", default="legacy-rotary")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries = read_entries(Path(args.docx))
    if not entries:
        raise SystemExit("No [single|timestamp] or [rotary|timestamp] entries found.")

    counts = {"single": 0, "rotary": 0}
    for entry in entries:
        counts[entry["source"]] += 1

    print(f"Found {len(entries)} entries:")
    print(f"  single: {counts['single']}")
    print(f"  rotary: {counts['rotary']}")
    print(f"  channel: {args.channel}")

    if args.dry_run:
        for entry in entries:
            preview = entry["body"].replace("\n", " ")[:100]
            print(f"[{entry['source']} | {entry['timestamp_text']}] {preview}")
        return

    instance_for = {
        "single": args.single_instance,
        "rotary": args.rotary_instance,
    }

    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")

        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        if not table:
            raise SystemExit("messages table does not exist in the target database.")

        inserted = 0
        skipped = 0
        with conn:
            for entry in entries:
                source = entry["source"]
                instance = instance_for[source]
                values = (
                    entry["created_at"],
                    args.channel,
                    source,
                    instance,
                    entry["body"],
                )

                exists = conn.execute(
                    """
                    SELECT 1
                    FROM messages
                    WHERE created_at = ?
                      AND channel = ?
                      AND source = ?
                      AND instance = ?
                      AND body = ?
                    LIMIT 1
                    """,
                    values,
                ).fetchone()

                if exists:
                    skipped += 1
                    continue

                conn.execute(
                    """
                    INSERT INTO messages
                        (created_at, channel, source, instance, kind, body, reply_to)
                    VALUES
                        (?, ?, ?, ?, 'message', ?, NULL)
                    """,
                    values,
                )
                inserted += 1

        print(f"Inserted: {inserted}")
        print(f"Skipped as duplicates: {skipped}")
        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        print(f"Total messages now in DB: {total}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
