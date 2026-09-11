use std::path::Path;

use rusqlite::{params, Connection, OptionalExtension, Result};

use crate::model::{ChannelMetadata, ChannelSummary, Identity, Message};

const SCHEMA: &str = include_str!("../schema.sql");

#[derive(Debug)]
pub enum NavigationAppendResult {
    Created(Message),
    Existing(Message),
    NonceConflict,
    ReplyTargetNotFound,
}

pub struct NavigationMessageInput<'a> {
    pub channel: &'a str,
    pub kind: &'a str,
    pub body: &'a str,
    pub reply_to: Option<i64>,
    pub nonce: &'a str,
    pub request_hash: &'a str,
}

pub fn connect(path: &Path) -> Result<Connection> {
    let conn = Connection::open(path)?;
    conn.execute_batch(
        "PRAGMA journal_mode = WAL;\nPRAGMA synchronous = NORMAL;\nPRAGMA busy_timeout = 5000;",
    )?;
    Ok(conn)
}

pub fn initialize(path: &Path) -> Result<()> {
    let conn = connect(path)?;
    conn.execute_batch(SCHEMA)?;
    migrate_web_participant_signing_columns(&conn)?;
    migrate_web_participant_totp_columns(&conn)?;
    migrate_web_participant_role(&conn)?;
    migrate_channel_metadata(&conn)?;
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_web_participants_public_key\n         ON web_participants(public_key)\n         WHERE public_key IS NOT NULL",
        [],
    )?;
    Ok(())
}

fn migrate_web_participant_signing_columns(conn: &Connection) -> Result<()> {
    if !table_has_column(conn, "web_participants", "public_key")? {
        conn.execute(
            "ALTER TABLE web_participants ADD COLUMN public_key TEXT",
            [],
        )?;
    }
    if !table_has_column(conn, "web_participants", "signature_scheme")? {
        conn.execute(
            "ALTER TABLE web_participants ADD COLUMN signature_scheme TEXT",
            [],
        )?;
    }
    Ok(())
}

fn migrate_web_participant_totp_columns(conn: &Connection) -> Result<()> {
    for (name, definition) in [
        ("totp_secret", "TEXT"),
        ("totp_last_step", "INTEGER"),
        ("totp_fail_count", "INTEGER NOT NULL DEFAULT 0"),
        ("totp_locked_until", "INTEGER"),
    ] {
        if !table_has_column(conn, "web_participants", name)? {
            conn.execute(
                &format!("ALTER TABLE web_participants ADD COLUMN {name} {definition}"),
                [],
            )?;
        }
    }
    Ok(())
}

fn migrate_web_participant_role(conn: &Connection) -> Result<()> {
    let added = !table_has_column(conn, "web_participants", "role")?;
    if added {
        conn.execute(
            "ALTER TABLE web_participants ADD COLUMN role TEXT NOT NULL DEFAULT 'user'",
            [],
        )?;
        conn.execute(
            "UPDATE web_participants SET role = 'admin', updated_at = unixepoch()\n             WHERE participant_id = 'cheng-main'",
            [],
        )?;
    }
    Ok(())
}

fn migrate_channel_metadata(conn: &Connection) -> Result<()> {
    conn.execute(
        "INSERT OR IGNORE INTO channels\n             (name, visibility, status, created_at, updated_at, created_by)\n         SELECT channel,\n                CASE WHEN channel = 'blackboard-lounge' THEN 'public' ELSE 'private' END,\n                'active',\n                MIN(created_at),\n                MAX(created_at),\n                NULL\n         FROM messages\n         GROUP BY channel",
        [],
    )?;
    Ok(())
}

fn table_has_column(conn: &Connection, table: &str, column: &str) -> Result<bool> {
    let sql = format!("PRAGMA table_info({table})");
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map([], |row| row.get::<_, String>(1))?;
    for name in rows {
        if name? == column {
            return Ok(true);
        }
    }
    Ok(false)
}

pub fn health(conn: &Connection) -> Result<()> {
    conn.query_row("SELECT 1", [], |_| Ok(()))?;
    Ok(())
}

pub fn list_channels(conn: &Connection) -> Result<Vec<ChannelSummary>> {
    list_channels_with_filter(conn, false)
}

pub fn list_public_channels(conn: &Connection) -> Result<Vec<ChannelSummary>> {
    list_channels_with_filter(conn, true)
}

fn list_channels_with_filter(conn: &Connection, public_only: bool) -> Result<Vec<ChannelSummary>> {
    let sql = if public_only {
        "SELECT c.name, COUNT(m.id) AS message_count, COALESCE(MAX(m.id), 0) AS last_id,\n                c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n         FROM channels c\n         LEFT JOIN messages m ON m.channel = c.name\n         WHERE c.visibility = 'public' AND c.status = 'active'\n         GROUP BY c.name, c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n         ORDER BY last_id DESC, c.name ASC"
    } else {
        "SELECT c.name, COUNT(m.id) AS message_count, COALESCE(MAX(m.id), 0) AS last_id,\n                c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n         FROM channels c\n         LEFT JOIN messages m ON m.channel = c.name\n         GROUP BY c.name, c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n         ORDER BY CASE c.status WHEN 'active' THEN 0 ELSE 1 END, last_id DESC, c.name ASC"
    };
    let mut stmt = conn.prepare(sql)?;
    let rows = stmt.query_map([], |row| {
        Ok(ChannelSummary {
            channel: row.get(0)?,
            message_count: row.get(1)?,
            last_id: row.get(2)?,
            visibility: row.get(3)?,
            status: row.get(4)?,
            created_at: row.get(5)?,
            updated_at: row.get(6)?,
            created_by: row.get(7)?,
        })
    })?;
    rows.collect()
}

pub fn channel_metadata(conn: &Connection, channel: &str) -> Result<Option<ChannelMetadata>> {
    conn.query_row(
        "SELECT name, visibility, status, created_at, updated_at, created_by\n         FROM channels WHERE name = ?1 LIMIT 1",
        [channel],
        |row| {
            Ok(ChannelMetadata {
                channel: row.get(0)?,
                visibility: row.get(1)?,
                status: row.get(2)?,
                created_at: row.get(3)?,
                updated_at: row.get(4)?,
                created_by: row.get(5)?,
            })
        },
    )
    .optional()
}

pub fn channel_is_public_active(conn: &Connection, channel: &str) -> Result<bool> {
    Ok(conn
        .query_row(
            "SELECT 1 FROM channels\n             WHERE name = ?1 AND visibility = 'public' AND status = 'active'",
            [channel],
            |_| Ok(1_i64),
        )
        .optional()?
        .is_some())
}

pub fn ensure_channel(conn: &Connection, channel: &str, created_by: Option<&str>) -> Result<()> {
    let visibility = if channel == "blackboard-lounge" {
        "public"
    } else {
        "private"
    };
    conn.execute(
        "INSERT OR IGNORE INTO channels (name, visibility, status, created_by)\n         VALUES (?1, ?2, 'active', ?3)",
        params![channel, visibility, created_by],
    )?;
    Ok(())
}

pub fn ensure_channel_for_write(
    conn: &Connection,
    channel: &str,
    created_by: Option<&str>,
) -> Result<bool> {
    ensure_channel(conn, channel, created_by)?;
    Ok(channel_metadata(conn, channel)?
        .map(|metadata| metadata.status == "active")
        .unwrap_or(false))
}

pub fn create_channel(
    conn: &Connection,
    channel: &str,
    visibility: &str,
    created_by: Option<&str>,
) -> Result<bool> {
    let changed = conn.execute(
        "INSERT OR IGNORE INTO channels (name, visibility, status, created_by)\n         VALUES (?1, ?2, 'active', ?3)",
        params![channel, visibility, created_by],
    )?;
    Ok(changed == 1)
}

pub fn update_channel(
    conn: &Connection,
    channel: &str,
    visibility: Option<&str>,
    status: Option<&str>,
) -> Result<bool> {
    let changed = match (visibility, status) {
        (Some(visibility), Some(status)) => conn.execute(
            "UPDATE channels\n             SET visibility = ?1, status = ?2, updated_at = unixepoch()\n             WHERE name = ?3",
            params![visibility, status, channel],
        )?,
        (Some(visibility), None) => conn.execute(
            "UPDATE channels\n             SET visibility = ?1, updated_at = unixepoch()\n             WHERE name = ?2",
            params![visibility, channel],
        )?,
        (None, Some(status)) => conn.execute(
            "UPDATE channels\n             SET status = ?1, updated_at = unixepoch()\n             WHERE name = ?2",
            params![status, channel],
        )?,
        (None, None) => 0,
    };
    Ok(changed == 1)
}

pub fn list_messages_after(
    conn: &Connection,
    after_id: i64,
    channel: Option<&str>,
    limit: usize,
) -> Result<Vec<Message>> {
    let sql = if channel.is_some() {
        "SELECT id, created_at, channel, source, instance, kind, body, reply_to\n         FROM messages\n         WHERE channel = ?1 AND id > ?2\n         ORDER BY id ASC\n         LIMIT ?3"
    } else {
        "SELECT id, created_at, channel, source, instance, kind, body, reply_to\n         FROM messages\n         WHERE id > ?1\n         ORDER BY id ASC\n         LIMIT ?2"
    };

    let mut stmt = conn.prepare(sql)?;
    let mut out = Vec::new();
    if let Some(channel) = channel {
        let rows = stmt.query_map(params![channel, after_id, limit as i64], row_to_message)?;
        for row in rows {
            out.push(row?);
        }
    } else {
        let rows = stmt.query_map(params![after_id, limit as i64], row_to_message)?;
        for row in rows {
            out.push(row?);
        }
    }
    Ok(out)
}

pub fn message_exists(conn: &Connection, id: i64) -> Result<bool> {
    Ok(conn
        .query_row("SELECT 1 FROM messages WHERE id = ?1", [id], |_| Ok(1_i64))
        .optional()?
        .is_some())
}

pub fn append_message(
    conn: &Connection,
    identity: &Identity,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
) -> Result<Message> {
    ensure_channel(conn, channel, Some(&identity.instance))?;
    conn.execute(
        "INSERT INTO messages (channel, source, instance, kind, body, reply_to)\n         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![channel, identity.source, identity.instance, kind, body, reply_to],
    )?;
    let id = conn.last_insert_rowid();
    message_by_id(conn, id)
}

pub fn append_navigation_message(
    conn: &Connection,
    identity: &Identity,
    input: NavigationMessageInput<'_>,
) -> Result<NavigationAppendResult> {
    let tx = conn.unchecked_transaction()?;

    let visibility = if input.channel == "blackboard-lounge" {
        "public"
    } else {
        "private"
    };
    tx.execute(
        "INSERT OR IGNORE INTO channels (name, visibility, status, created_by)\n         VALUES (?1, ?2, 'active', ?3)",
        params![input.channel, visibility, identity.instance],
    )?;

    let reserved = tx.execute(
        "INSERT OR IGNORE INTO navigation_writes\n             (instance, nonce, request_hash, message_id)\n         VALUES (?1, ?2, ?3, 0)",
        params![identity.instance, input.nonce, input.request_hash],
    )?;

    if reserved == 0 {
        let (stored_hash, message_id): (String, i64) = tx.query_row(
            "SELECT request_hash, message_id\n             FROM navigation_writes\n             WHERE instance = ?1 AND nonce = ?2",
            params![identity.instance, input.nonce],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;

        if stored_hash != input.request_hash {
            return Ok(NavigationAppendResult::NonceConflict);
        }
        let message = message_by_id(&tx, message_id)?;
        return Ok(NavigationAppendResult::Existing(message));
    }

    if let Some(target) = input.reply_to {
        let exists = tx
            .query_row("SELECT 1 FROM messages WHERE id = ?1", [target], |_| {
                Ok(1_i64)
            })
            .optional()?
            .is_some();
        if !exists {
            return Ok(NavigationAppendResult::ReplyTargetNotFound);
        }
    }

    tx.execute(
        "INSERT INTO messages (channel, source, instance, kind, body, reply_to)\n         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![
            input.channel,
            identity.source,
            identity.instance,
            input.kind,
            input.body,
            input.reply_to
        ],
    )?;
    let message_id = tx.last_insert_rowid();

    tx.execute(
        "UPDATE navigation_writes\n         SET message_id = ?1\n         WHERE instance = ?2 AND nonce = ?3",
        params![message_id, identity.instance, input.nonce],
    )?;

    let message = message_by_id(&tx, message_id)?;
    tx.commit()?;
    Ok(NavigationAppendResult::Created(message))
}

fn message_by_id(conn: &Connection, id: i64) -> Result<Message> {
    conn.query_row(
        "SELECT id, created_at, channel, source, instance, kind, body, reply_to\n         FROM messages WHERE id = ?1",
        [id],
        row_to_message,
    )
}

fn row_to_message(row: &rusqlite::Row<'_>) -> Result<Message> {
    Ok(Message {
        id: row.get(0)?,
        created_at: row.get(1)?,
        channel: row.get(2)?,
        source: row.get(3)?,
        instance: row.get(4)?,
        kind: row.get(5)?,
        body: row.get(6)?,
        reply_to: row.get(7)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn initialize_migrates_existing_web_participant_table() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE web_participants (\n                    participant_id TEXT PRIMARY KEY,\n                    source TEXT NOT NULL,\n                    label TEXT,\n                    key_hash TEXT UNIQUE,\n                    created_at INTEGER NOT NULL DEFAULT (unixepoch()),\n                    updated_at INTEGER NOT NULL DEFAULT (unixepoch())\n                );\n                INSERT INTO web_participants (participant_id, source, label)\n                VALUES ('cheng-main', 'cheng', 'Cheng');",
            )
            .unwrap();
        }

        initialize(&path).unwrap();
        let conn = connect(&path).unwrap();
        assert!(table_has_column(&conn, "web_participants", "public_key").unwrap());
        assert!(table_has_column(&conn, "web_participants", "signature_scheme").unwrap());
        assert!(table_has_column(&conn, "web_participants", "totp_secret").unwrap());
        assert!(table_has_column(&conn, "web_participants", "totp_last_step").unwrap());
        assert!(table_has_column(&conn, "web_participants", "totp_fail_count").unwrap());
        assert!(table_has_column(&conn, "web_participants", "totp_locked_until").unwrap());
        assert!(table_has_column(&conn, "web_participants", "role").unwrap());
        let role: String = conn
            .query_row(
                "SELECT role FROM web_participants WHERE participant_id = 'cheng-main'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(role, "admin");
    }

    #[test]
    fn channel_metadata_defaults_private_except_lounge_and_filters_guests() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        initialize(&path).unwrap();
        let conn = connect(&path).unwrap();
        let identity = Identity {
            source: "test".into(),
            instance: "test-main".into(),
            label: None,
        };

        append_message(
            &conn,
            &identity,
            "control-systems",
            "message",
            "private",
            None,
        )
        .unwrap();
        append_message(
            &conn,
            &identity,
            "blackboard-lounge",
            "message",
            "public",
            None,
        )
        .unwrap();

        assert_eq!(
            channel_metadata(&conn, "control-systems")
                .unwrap()
                .unwrap()
                .visibility,
            "private"
        );
        assert!(channel_is_public_active(&conn, "blackboard-lounge").unwrap());
        let guest = list_public_channels(&conn).unwrap();
        assert_eq!(guest.len(), 1);
        assert_eq!(guest[0].channel, "blackboard-lounge");
    }

    #[test]
    fn navigation_write_is_idempotent_by_identity_and_nonce() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        initialize(&path).unwrap();
        let conn = connect(&path).unwrap();
        let identity = Identity {
            source: "single".into(),
            instance: "legacy-single".into(),
            label: None,
        };

        let first = append_navigation_message(
            &conn,
            &identity,
            NavigationMessageInput {
                channel: "control-systems",
                kind: "message",
                body: "hello",
                reply_to: None,
                nonce: "nonce-1",
                request_hash: "hash-a",
            },
        )
        .unwrap();
        let first_id = match first {
            NavigationAppendResult::Created(message) => message.id,
            other => panic!("unexpected result: {other:?}"),
        };

        let second = append_navigation_message(
            &conn,
            &identity,
            NavigationMessageInput {
                channel: "control-systems",
                kind: "message",
                body: "hello",
                reply_to: None,
                nonce: "nonce-1",
                request_hash: "hash-a",
            },
        )
        .unwrap();
        match second {
            NavigationAppendResult::Existing(message) => assert_eq!(message.id, first_id),
            other => panic!("unexpected result: {other:?}"),
        }

        let conflict = append_navigation_message(
            &conn,
            &identity,
            NavigationMessageInput {
                channel: "control-systems",
                kind: "message",
                body: "different",
                reply_to: None,
                nonce: "nonce-1",
                request_hash: "hash-b",
            },
        )
        .unwrap();
        assert!(matches!(conflict, NavigationAppendResult::NonceConflict));

        let messages = list_messages_after(&conn, 0, Some("control-systems"), 10).unwrap();
        assert_eq!(messages.len(), 1);
    }
}
