use std::path::Path;

use rusqlite::{params, Connection, OpenFlags, OptionalExtension, Result};

use crate::{
    authorization_admin, execution,
    model::{ChannelMetadata, ChannelSummary, Identity, Message},
};

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

pub fn connect_read_only(path: &Path) -> Result<Connection> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    conn.busy_timeout(std::time::Duration::from_millis(5000))?;
    conn.execute_batch("PRAGMA query_only = ON;")?;
    Ok(conn)
}

pub fn initialize(path: &Path) -> Result<()> {
    let conn = connect(path)?;
    conn.execute_batch(SCHEMA)?;
    migrate_web_participant_auth_columns(&conn)?;
    migrate_web_participant_totp_columns(&conn)?;
    migrate_web_participant_role(&conn)?;
    migrate_web_participant_status(&conn)?;
    migrate_message_conversation_ref(&conn)?;
    migrate_channel_metadata(&conn)?;
    authorization_admin::migrate_schema(&conn)?;
    execution::migrate_execution_schema(&conn)?;
    Ok(())
}

fn migrate_web_participant_auth_columns(conn: &Connection) -> Result<()> {
    if !table_has_column(conn, "web_participants", "auth_scheme")? {
        conn.execute(
            "ALTER TABLE web_participants ADD COLUMN auth_scheme TEXT",
            [],
        )?;
    }
    if !table_has_column(conn, "web_participants", "auth_secret")? {
        conn.execute(
            "ALTER TABLE web_participants ADD COLUMN auth_secret TEXT",
            [],
        )?;
    }

    // Ed25519 is intentionally not migrated. Existing asymmetric credentials are
    // discarded; participants must receive a fresh HMAC secret after deployment.
    conn.execute("DROP INDEX IF EXISTS idx_web_participants_public_key", [])?;
    if table_has_column(conn, "web_participants", "public_key")? {
        conn.execute("ALTER TABLE web_participants DROP COLUMN public_key", [])?;
    }
    if table_has_column(conn, "web_participants", "signature_scheme")? {
        conn.execute(
            "ALTER TABLE web_participants DROP COLUMN signature_scheme",
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

fn migrate_web_participant_status(conn: &Connection) -> Result<()> {
    if !table_has_column(conn, "web_participants", "status")? {
        conn.execute(
            "ALTER TABLE web_participants ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            [],
        )?;
    }
    Ok(())
}

fn migrate_message_conversation_ref(conn: &Connection) -> Result<()> {
    if table_has_column(conn, "messages", "conversation_ref")? {
        return Ok(());
    }
    if table_has_column(conn, "messages", "conversation_uuid")? {
        conn.execute(
            "ALTER TABLE messages RENAME COLUMN conversation_uuid TO conversation_ref",
            [],
        )?;
    } else {
        conn.execute("ALTER TABLE messages ADD COLUMN conversation_ref TEXT", [])?;
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

pub const DEFAULT_CHANNEL_DIRECTORY_WINDOW_SIZE: usize = 20;
pub const MAX_CHANNEL_DIRECTORY_WINDOW_SIZE: usize = 200;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChannelDirectoryWindowRequest<'a> {
    pub after_name: Option<&'a str>,
    pub limit: Option<usize>,
    pub public_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChannelDirectoryWindow {
    pub channels: Vec<ChannelSummary>,
    pub has_more: bool,
}

pub fn read_channel_directory_window(
    conn: &Connection,
    request: ChannelDirectoryWindowRequest<'_>,
) -> Result<ChannelDirectoryWindow> {
    if request.after_name.is_some_and(|value| value.is_empty()) {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let limit = request
        .limit
        .unwrap_or(DEFAULT_CHANNEL_DIRECTORY_WINDOW_SIZE);
    if !(1..=MAX_CHANNEL_DIRECTORY_WINDOW_SIZE).contains(&limit) {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let fetch_limit = i64::try_from(limit + 1).map_err(|_| rusqlite::Error::InvalidQuery)?;

    let mut channels =
        read_channel_directory_rows(conn, request.public_only, request.after_name, fetch_limit)?;
    let has_more = channels.len() > limit;
    channels.truncate(limit);
    Ok(ChannelDirectoryWindow { channels, has_more })
}

fn read_channel_directory_rows(
    conn: &Connection,
    public_only: bool,
    after_name: Option<&str>,
    fetch_limit: i64,
) -> Result<Vec<ChannelSummary>> {
    let mut out = Vec::new();
    match (public_only, after_name) {
        (true, Some(after_name)) => {
            let mut stmt = conn.prepare(
                "SELECT c.name, COUNT(m.id) AS message_count, COALESCE(MAX(m.id), 0) AS last_id,\n                        c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n                 FROM channels c\n                 LEFT JOIN messages m ON m.channel = c.name\n                 WHERE c.visibility = 'public' AND c.status = 'active' AND c.name > ?1\n                 GROUP BY c.name, c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n                 ORDER BY c.name ASC\n                 LIMIT ?2",
            )?;
            let rows = stmt.query_map(params![after_name, fetch_limit], channel_summary_row)?;
            for row in rows {
                out.push(row?);
            }
        }
        (true, None) => {
            let mut stmt = conn.prepare(
                "SELECT c.name, COUNT(m.id) AS message_count, COALESCE(MAX(m.id), 0) AS last_id,\n                        c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n                 FROM channels c\n                 LEFT JOIN messages m ON m.channel = c.name\n                 WHERE c.visibility = 'public' AND c.status = 'active'\n                 GROUP BY c.name, c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n                 ORDER BY c.name ASC\n                 LIMIT ?1",
            )?;
            let rows = stmt.query_map([fetch_limit], channel_summary_row)?;
            for row in rows {
                out.push(row?);
            }
        }
        (false, Some(after_name)) => {
            let mut stmt = conn.prepare(
                "SELECT c.name, COUNT(m.id) AS message_count, COALESCE(MAX(m.id), 0) AS last_id,\n                        c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n                 FROM channels c\n                 LEFT JOIN messages m ON m.channel = c.name\n                 WHERE c.name > ?1\n                 GROUP BY c.name, c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n                 ORDER BY c.name ASC\n                 LIMIT ?2",
            )?;
            let rows = stmt.query_map(params![after_name, fetch_limit], channel_summary_row)?;
            for row in rows {
                out.push(row?);
            }
        }
        (false, None) => {
            let mut stmt = conn.prepare(
                "SELECT c.name, COUNT(m.id) AS message_count, COALESCE(MAX(m.id), 0) AS last_id,\n                        c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n                 FROM channels c\n                 LEFT JOIN messages m ON m.channel = c.name\n                 GROUP BY c.name, c.visibility, c.status, c.created_at, c.updated_at, c.created_by\n                 ORDER BY c.name ASC\n                 LIMIT ?1",
            )?;
            let rows = stmt.query_map([fetch_limit], channel_summary_row)?;
            for row in rows {
                out.push(row?);
            }
        }
    }
    Ok(out)
}

fn channel_summary_row(row: &rusqlite::Row<'_>) -> Result<ChannelSummary> {
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
        "SELECT id, created_at, channel, source, instance, conversation_ref, kind, body, reply_to\n         FROM messages\n         WHERE channel = ?1 AND id > ?2\n         ORDER BY id ASC\n         LIMIT ?3"
    } else {
        "SELECT id, created_at, channel, source, instance, conversation_ref, kind, body, reply_to\n         FROM messages\n         WHERE id > ?1\n         ORDER BY id ASC\n         LIMIT ?2"
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
    append_navigation_message_with_conversation(conn, identity, input, None)
}

pub fn append_navigation_message_with_conversation(
    conn: &Connection,
    identity: &Identity,
    input: NavigationMessageInput<'_>,
    conversation_ref: Option<&str>,
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
        "INSERT INTO messages (channel, source, instance, conversation_ref, kind, body, reply_to)\n         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![
            input.channel,
            identity.source,
            identity.instance,
            conversation_ref,
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
        "SELECT id, created_at, channel, source, instance, conversation_ref, kind, body, reply_to\n         FROM messages WHERE id = ?1",
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
        conversation_ref: row.get(5)?,
        kind: row.get(6)?,
        body: row.get(7)?,
        reply_to: row.get(8)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn initialize_replaces_ed25519_registry_columns_with_hmac_auth_columns() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE web_participants (\n                    participant_id TEXT PRIMARY KEY,\n                    source TEXT NOT NULL,\n                    label TEXT,\n                    public_key TEXT,\n                    signature_scheme TEXT,\n                    created_at INTEGER NOT NULL DEFAULT (unixepoch()),\n                    updated_at INTEGER NOT NULL DEFAULT (unixepoch())\n                );\n                INSERT INTO web_participants (participant_id, source, label, public_key, signature_scheme)\n                VALUES ('cheng-main', 'cheng', 'Cheng', 'old-ed25519-key', 'ed25519-v1');",
            )
            .unwrap();
        }

        initialize(&path).unwrap();
        let conn = connect(&path).unwrap();
        assert!(table_has_column(&conn, "web_participants", "auth_secret").unwrap());
        assert!(table_has_column(&conn, "web_participants", "auth_scheme").unwrap());
        assert!(!table_has_column(&conn, "web_participants", "public_key").unwrap());
        assert!(!table_has_column(&conn, "web_participants", "signature_scheme").unwrap());
        assert!(table_has_column(&conn, "web_participants", "totp_secret").unwrap());
        assert!(table_has_column(&conn, "web_participants", "role").unwrap());
        assert!(table_has_column(&conn, "web_participants", "status").unwrap());
        assert!(table_has_column(&conn, "messages", "conversation_ref").unwrap());
        let (role, status, auth_scheme, auth_secret): (
            String,
            String,
            Option<String>,
            Option<String>,
        ) = conn
            .query_row(
                "SELECT role, status, auth_scheme, auth_secret FROM web_participants WHERE participant_id = 'cheng-main'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .unwrap();
        assert_eq!(role, "admin");
        assert_eq!(status, "active");
        assert!(auth_scheme.is_none());
        assert!(auth_secret.is_none());
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

        let private = append_message(
            &conn,
            &identity,
            "control-systems",
            "message",
            "private",
            None,
        )
        .unwrap();
        assert!(private.conversation_ref.is_none());
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
        let guest = read_channel_directory_window(
            &conn,
            ChannelDirectoryWindowRequest {
                after_name: None,
                limit: None,
                public_only: true,
            },
        )
        .unwrap();
        assert_eq!(guest.channels.len(), 1);
        assert_eq!(guest.channels[0].channel, "blackboard-lounge");
        assert!(!guest.has_more);
    }

    #[test]
    fn channel_directory_window_is_bounded_and_stable_under_message_activity() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        initialize(&path).unwrap();
        let conn = connect(&path).unwrap();
        let identity = Identity {
            source: "test".into(),
            instance: "test-main".into(),
            label: None,
        };

        for name in ["alpha", "bravo", "charlie", "delta", "echo"] {
            append_message(&conn, &identity, name, "message", name, None).unwrap();
        }

        let first = read_channel_directory_window(
            &conn,
            ChannelDirectoryWindowRequest {
                after_name: None,
                limit: Some(2),
                public_only: false,
            },
        )
        .unwrap();
        assert_eq!(
            first
                .channels
                .iter()
                .map(|channel| channel.channel.as_str())
                .collect::<Vec<_>>(),
            vec!["alpha", "bravo"]
        );
        assert!(first.has_more);

        // Message activity changes last_id/count but must not perturb the name-keyset cursor.
        append_message(&conn, &identity, "alpha", "message", "new activity", None).unwrap();
        append_message(&conn, &identity, "echo", "message", "new activity", None).unwrap();

        let second = read_channel_directory_window(
            &conn,
            ChannelDirectoryWindowRequest {
                after_name: Some(first.channels.last().unwrap().channel.as_str()),
                limit: Some(2),
                public_only: false,
            },
        )
        .unwrap();
        assert_eq!(
            second
                .channels
                .iter()
                .map(|channel| channel.channel.as_str())
                .collect::<Vec<_>>(),
            vec!["charlie", "delta"]
        );
        assert!(second.has_more);

        let third = read_channel_directory_window(
            &conn,
            ChannelDirectoryWindowRequest {
                after_name: Some(second.channels.last().unwrap().channel.as_str()),
                limit: Some(2),
                public_only: false,
            },
        )
        .unwrap();
        assert_eq!(
            third
                .channels
                .iter()
                .map(|channel| channel.channel.as_str())
                .collect::<Vec<_>>(),
            vec!["echo"]
        );
        assert!(!third.has_more);
    }

    #[test]
    fn channel_directory_window_public_filter_and_limits_are_canonical() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        initialize(&path).unwrap();
        let conn = connect(&path).unwrap();
        let identity = Identity {
            source: "test".into(),
            instance: "test-main".into(),
            label: None,
        };

        for name in ["blackboard-lounge", "private-a", "public-b", "public-c"] {
            append_message(&conn, &identity, name, "message", name, None).unwrap();
        }
        assert!(update_channel(&conn, "public-b", Some("public"), None).unwrap());
        assert!(update_channel(&conn, "public-c", Some("public"), Some("archived")).unwrap());

        let window = read_channel_directory_window(
            &conn,
            ChannelDirectoryWindowRequest {
                after_name: None,
                limit: None,
                public_only: true,
            },
        )
        .unwrap();
        assert_eq!(
            window
                .channels
                .iter()
                .map(|channel| channel.channel.as_str())
                .collect::<Vec<_>>(),
            vec!["blackboard-lounge", "public-b"]
        );
        assert!(!window.has_more);
        assert!(window
            .channels
            .iter()
            .all(|channel| channel.visibility == "public" && channel.status == "active"));

        for request in [
            ChannelDirectoryWindowRequest {
                after_name: Some(""),
                limit: Some(1),
                public_only: false,
            },
            ChannelDirectoryWindowRequest {
                after_name: None,
                limit: Some(0),
                public_only: false,
            },
            ChannelDirectoryWindowRequest {
                after_name: None,
                limit: Some(MAX_CHANNEL_DIRECTORY_WINDOW_SIZE + 1),
                public_only: false,
            },
        ] {
            assert!(read_channel_directory_window(&conn, request).is_err());
        }
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

        let first = append_navigation_message_with_conversation(
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
            Some("claude-chat-abc123"),
        )
        .unwrap();
        let first_id = match first {
            NavigationAppendResult::Created(message) => {
                assert_eq!(
                    message.conversation_ref.as_deref(),
                    Some("claude-chat-abc123")
                );
                message.id
            }
            other => panic!("unexpected result: {other:?}"),
        };

        let second = append_navigation_message_with_conversation(
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
            Some("claude-chat-abc123"),
        )
        .unwrap();
        match second {
            NavigationAppendResult::Existing(message) => {
                assert_eq!(message.id, first_id);
                assert_eq!(
                    message.conversation_ref.as_deref(),
                    Some("claude-chat-abc123")
                );
            }
            other => panic!("unexpected result: {other:?}"),
        }

        let conflict = append_navigation_message_with_conversation(
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
            Some("other-chat"),
        )
        .unwrap();
        assert!(matches!(conflict, NavigationAppendResult::NonceConflict));

        let messages = list_messages_after(&conn, 0, Some("control-systems"), 10).unwrap();
        assert_eq!(messages.len(), 1);
        assert_eq!(
            messages[0].conversation_ref.as_deref(),
            Some("claude-chat-abc123")
        );
    }

    #[test]
    fn migration_preserves_historical_messages_with_null_conversation_ref() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE messages (\n                    id INTEGER PRIMARY KEY,\n                    created_at INTEGER NOT NULL DEFAULT (unixepoch()),\n                    channel TEXT NOT NULL,\n                    source TEXT NOT NULL,\n                    instance TEXT NOT NULL,\n                    kind TEXT NOT NULL DEFAULT 'message',\n                    body TEXT NOT NULL,\n                    reply_to INTEGER\n                );\n                INSERT INTO messages (channel, source, instance, kind, body)\n                VALUES ('blackboard-lounge', 'legacy', 'legacy-main', 'message', 'before migration');",
            )
            .unwrap();
        }

        initialize(&path).unwrap();
        let conn = connect(&path).unwrap();
        let messages = list_messages_after(&conn, 0, Some("blackboard-lounge"), 10).unwrap();
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0].body, "before migration");
        assert!(messages[0].conversation_ref.is_none());
    }

    #[test]
    fn migration_renames_legacy_conversation_uuid_column_and_preserves_value() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE messages (\n\
                    id INTEGER PRIMARY KEY,\n\
                    created_at INTEGER NOT NULL DEFAULT (unixepoch()),\n\
                    channel TEXT NOT NULL,\n\
                    source TEXT NOT NULL,\n\
                    instance TEXT NOT NULL,\n\
                    conversation_uuid TEXT,\n\
                    kind TEXT NOT NULL DEFAULT 'message',\n\
                    body TEXT NOT NULL,\n\
                    reply_to INTEGER\n\
                );\n\
                INSERT INTO messages\n\
                    (channel, source, instance, conversation_uuid, kind, body)\n\
                VALUES\n\
                    ('blackboard-lounge', 'legacy', 'legacy-main', 'legacy-chat-42', 'message', 'before rename');",
            )
            .unwrap();
        }

        initialize(&path).unwrap();
        let conn = connect(&path).unwrap();
        assert!(table_has_column(&conn, "messages", "conversation_ref").unwrap());
        assert!(!table_has_column(&conn, "messages", "conversation_uuid").unwrap());
        let messages = list_messages_after(&conn, 0, Some("blackboard-lounge"), 10).unwrap();
        assert_eq!(messages.len(), 1);
        assert_eq!(
            messages[0].conversation_ref.as_deref(),
            Some("legacy-chat-42")
        );
    }
}
