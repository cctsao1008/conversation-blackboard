from pathlib import Path

path = Path('src/db.rs')
text = path.read_text()

anchor = '''pub fn list_public_channels(conn: &Connection) -> Result<Vec<ChannelSummary>> {\n    list_channels_with_filter(conn, true)\n}\n\n'''
if anchor not in text:
    raise RuntimeError('channel reader anchor not found')

insert = r'''pub const DEFAULT_CHANNEL_DIRECTORY_WINDOW_SIZE: usize = 20;
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

    let mut channels = read_channel_directory_rows(
        conn,
        request.public_only,
        request.after_name,
        fetch_limit,
    )?;
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

'''
text = text.replace(anchor, anchor + insert, 1)

# Reuse the shared row projector in the legacy full reader without changing its ordering.
old = '''    let rows = stmt.query_map([], |row| {\n        Ok(ChannelSummary {\n            channel: row.get(0)?,\n            message_count: row.get(1)?,\n            last_id: row.get(2)?,\n            visibility: row.get(3)?,\n            status: row.get(4)?,\n            created_at: row.get(5)?,\n            updated_at: row.get(6)?,\n            created_by: row.get(7)?,\n        })\n    })?;\n'''
new = '''    let rows = stmt.query_map([], channel_summary_row)?;\n'''
if old not in text:
    raise RuntimeError('legacy channel row projector anchor not found')
text = text.replace(old, new, 1)

test_anchor = '''    #[test]\n    fn navigation_write_is_idempotent_by_identity_and_nonce() {\n'''
if test_anchor not in text:
    raise RuntimeError('channel test anchor not found')

tests = r'''    #[test]
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

'''
text = text.replace(test_anchor, tests + test_anchor, 1)
path.write_text(text)
