use std::path::Path;

use rusqlite::{params, Connection, OptionalExtension, Result};

use crate::model::{ChannelSummary, Identity, Message};

const SCHEMA: &str = include_str!("../schema.sql");

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
    Ok(())
}

pub fn health(conn: &Connection) -> Result<()> {
    conn.query_row("SELECT 1", [], |_| Ok(()))?;
    Ok(())
}

pub fn list_channels(conn: &Connection) -> Result<Vec<ChannelSummary>> {
    let mut stmt = conn.prepare(
        "SELECT channel, COUNT(*) AS message_count, MAX(id) AS last_id\n         FROM messages\n         GROUP BY channel\n         ORDER BY last_id DESC",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok(ChannelSummary {
            channel: row.get(0)?,
            message_count: row.get(1)?,
            last_id: row.get(2)?,
        })
    })?;
    rows.collect()
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
    conn.execute(
        "INSERT INTO messages (channel, source, instance, kind, body, reply_to)\n         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![channel, identity.source, identity.instance, kind, body, reply_to],
    )?;
    let id = conn.last_insert_rowid();
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
