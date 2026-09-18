from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

# Canonical bounded reader and migration-owned index.
path = Path("src/authorization_admin.rs")
text = path.read_text()
text = replace_once(
    text,
    "use rusqlite::{params, Connection, OptionalExtension, Transaction};",
    "use rusqlite::{params, params_from_iter, types::Value as SqlValue, Connection, OptionalExtension, Transaction};",
    "rusqlite imports",
)
text = replace_once(
    text,
    "const MAX_SURFACE_BYTES: usize = 64;\n",
    """const MAX_SURFACE_BYTES: usize = 64;\n\npub const DEFAULT_ADMINISTRATION_HISTORY_WINDOW_SIZE: usize = 20;\npub const MAX_ADMINISTRATION_HISTORY_WINDOW_SIZE: usize = 200;\n\n#[derive(Debug, Clone, Copy, PartialEq, Eq)]\npub enum AdministrationHistoryOrder {\n    Desc,\n    Asc,\n}\n\nimpl AdministrationHistoryOrder {\n    pub fn parse(value: Option<&str>) -> Option<Self> {\n        match value.unwrap_or(\"desc\") {\n            \"desc\" => Some(Self::Desc),\n            \"asc\" => Some(Self::Asc),\n            _ => None,\n        }\n    }\n\n    pub const fn as_str(self) -> &'static str {\n        match self {\n            Self::Desc => \"desc\",\n            Self::Asc => \"asc\",\n        }\n    }\n}\n\n#[derive(Debug, Clone, Copy)]\npub struct AuthorizationAdministrationHistoryWindowRequest<'a> {\n    pub participant_id: Option<&'a str>,\n    pub before: Option<i64>,\n    pub after: Option<i64>,\n    pub limit: Option<usize>,\n    pub order: Option<&'a str>,\n}\n\n#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct AuthorizationAdministrationHistoryWindow {\n    pub events: Vec<AuthorizationAdministrationEvent>,\n    pub order: String,\n    pub has_more: bool,\n}\n""",
    "history window types",
)
text = replace_once(
    text,
    """        CREATE INDEX IF NOT EXISTS idx_authorization_admin_events_grant\n            ON authorization_admin_events (grant_store, grant_id, id);\n""",
    """        CREATE INDEX IF NOT EXISTS idx_authorization_admin_events_grant\n            ON authorization_admin_events (grant_store, grant_id, id);\n        CREATE INDEX IF NOT EXISTS idx_authorization_admin_events_participant_id\n            ON authorization_admin_events (participant_id, id);\n""",
    "participant history index",
)
window_fn = r'''
pub fn read_administration_event_window(
    conn: &Connection,
    request: AuthorizationAdministrationHistoryWindowRequest<'_>,
) -> rusqlite::Result<AuthorizationAdministrationHistoryWindow> {
    if !schema_current(conn)? {
        return Err(rusqlite::Error::InvalidQuery);
    }
    if request.before.is_some_and(|value| value <= 0)
        || request.after.is_some_and(|value| value <= 0)
        || (request.before.is_some() && request.after.is_some())
    {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let order = AdministrationHistoryOrder::parse(request.order)
        .ok_or(rusqlite::Error::InvalidQuery)?;
    if (order == AdministrationHistoryOrder::Desc && request.after.is_some())
        || (order == AdministrationHistoryOrder::Asc && request.before.is_some())
    {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let limit = request
        .limit
        .unwrap_or(DEFAULT_ADMINISTRATION_HISTORY_WINDOW_SIZE);
    if !(1..=MAX_ADMINISTRATION_HISTORY_WINDOW_SIZE).contains(&limit) {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let participant_id = request
        .participant_id
        .map(|value| identity::validate_participant_id(value).ok_or(rusqlite::Error::InvalidQuery))
        .transpose()?;

    let mut query = String::from(
        "SELECT id, grant_store, grant_id, operation, actor_surface, actor_provider, actor_subject,
                actor_participant_id, target_principal_provider, target_principal_subject,
                participant_id, capability, resource, intent_id, expires_at, one_shot,
                before_status, after_status, created_at
         FROM authorization_admin_events",
    );
    let mut predicates = Vec::new();
    let mut values = Vec::<SqlValue>::new();
    if let Some(participant_id) = participant_id {
        predicates.push("participant_id = ?");
        values.push(SqlValue::Text(participant_id));
    }
    match order {
        AdministrationHistoryOrder::Desc => {
            if let Some(before) = request.before {
                predicates.push("id < ?");
                values.push(SqlValue::Integer(before));
            }
        }
        AdministrationHistoryOrder::Asc => {
            if let Some(after) = request.after {
                predicates.push("id > ?");
                values.push(SqlValue::Integer(after));
            }
        }
    }
    if !predicates.is_empty() {
        query.push_str(" WHERE ");
        query.push_str(&predicates.join(" AND "));
    }
    match order {
        AdministrationHistoryOrder::Desc => query.push_str(" ORDER BY id DESC"),
        AdministrationHistoryOrder::Asc => query.push_str(" ORDER BY id ASC"),
    }
    query.push_str(" LIMIT ?");
    values.push(SqlValue::Integer((limit + 1) as i64));

    let mut stmt = conn.prepare(&query)?;
    let rows = stmt
        .query_map(params_from_iter(values), |row| {
            Ok(AuthorizationAdministrationEvent {
                id: row.get(0)?,
                grant_store: row.get(1)?,
                grant_id: row.get(2)?,
                operation: row.get(3)?,
                actor_surface: row.get(4)?,
                actor_provider: row.get(5)?,
                actor_subject: row.get(6)?,
                actor_participant_id: row.get(7)?,
                target_principal_provider: row.get(8)?,
                target_principal_subject: row.get(9)?,
                participant_id: row.get(10)?,
                capability: row.get(11)?,
                resource: row.get(12)?,
                intent_id: row.get(13)?,
                expires_at: row.get(14)?,
                one_shot: row.get::<_, i64>(15)? != 0,
                before_status: row.get(16)?,
                after_status: row.get(17)?,
                created_at: row.get(18)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let has_more = rows.len() > limit;
    let mut events = rows;
    events.truncate(limit);
    Ok(AuthorizationAdministrationHistoryWindow {
        events,
        order: order.as_str().to_owned(),
        has_more,
    })
}

'''
text = replace_once(
    text,
    "fn normalize_durable_grant_create_request(\n",
    window_fn + "fn normalize_durable_grant_create_request(\n",
    "bounded history reader",
)
window_tests = r'''
    #[test]
    fn administration_history_window_is_bounded_and_pages_without_overlap() {
        let (_dir, conn) = setup();
        let actor = AuthorizationAdministrationActor::local_cli();
        for index in 0..25 {
            let subject = format!("history-agent-{index}");
            create_durable_grant(
                &conn,
                &actor,
                &DurableGrantCreateRequest {
                    principal_provider: "oidc:https://issuer.example",
                    principal_subject: &subject,
                    participant_id: "maker-main",
                    capability: authorization::READ_MESSAGES,
                    resource: None,
                },
            )
            .unwrap();
        }

        let first = read_administration_event_window(
            &conn,
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: None,
                before: None,
                after: None,
                limit: None,
                order: None,
            },
        )
        .unwrap();
        assert_eq!(first.order, "desc");
        assert_eq!(first.events.len(), DEFAULT_ADMINISTRATION_HISTORY_WINDOW_SIZE);
        assert!(first.has_more);
        assert_eq!(first.events.first().unwrap().id, 25);
        assert_eq!(first.events.last().unwrap().id, 6);

        let second = read_administration_event_window(
            &conn,
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: None,
                before: Some(6),
                after: None,
                limit: None,
                order: Some("desc"),
            },
        )
        .unwrap();
        assert_eq!(second.events.iter().map(|event| event.id).collect::<Vec<_>>(), vec![5, 4, 3, 2, 1]);
        assert!(!second.has_more);

        let ascending = read_administration_event_window(
            &conn,
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: None,
                before: None,
                after: Some(20),
                limit: Some(20),
                order: Some("asc"),
            },
        )
        .unwrap();
        assert_eq!(ascending.events.iter().map(|event| event.id).collect::<Vec<_>>(), vec![21, 22, 23, 24, 25]);
        assert!(!ascending.has_more);
    }

    #[test]
    fn administration_history_window_validates_direction_limit_and_filter() {
        let (_dir, conn) = setup();
        let invalid = [
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: None,
                before: Some(2),
                after: Some(1),
                limit: Some(20),
                order: Some("desc"),
            },
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: None,
                before: None,
                after: Some(1),
                limit: Some(20),
                order: Some("desc"),
            },
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: None,
                before: Some(1),
                after: None,
                limit: Some(20),
                order: Some("asc"),
            },
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: None,
                before: None,
                after: None,
                limit: Some(0),
                order: None,
            },
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: None,
                before: None,
                after: None,
                limit: Some(MAX_ADMINISTRATION_HISTORY_WINDOW_SIZE + 1),
                order: None,
            },
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: Some("bad participant"),
                before: None,
                after: None,
                limit: None,
                order: None,
            },
        ];
        for request in invalid {
            assert!(read_administration_event_window(&conn, request).is_err());
        }
    }

    #[test]
    fn administration_history_window_filters_by_participant_and_schema_owns_index() {
        let (_dir, conn) = setup();
        identity::provision_web_participant_identity(&conn, "other-main", "other", Some("Other"))
            .unwrap()
            .unwrap();
        let actor = AuthorizationAdministrationActor::local_cli();
        for (participant_id, subject) in [
            ("maker-main", "maker-history-1"),
            ("other-main", "other-history-1"),
            ("maker-main", "maker-history-2"),
        ] {
            create_durable_grant(
                &conn,
                &actor,
                &DurableGrantCreateRequest {
                    principal_provider: "oidc:https://issuer.example",
                    principal_subject: subject,
                    participant_id,
                    capability: authorization::READ_MESSAGES,
                    resource: None,
                },
            )
            .unwrap();
        }
        let window = read_administration_event_window(
            &conn,
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: Some("maker-main"),
                before: None,
                after: None,
                limit: Some(10),
                order: Some("asc"),
            },
        )
        .unwrap();
        assert_eq!(window.events.len(), 2);
        assert!(window.events.iter().all(|event| event.participant_id == "maker-main"));
        assert!(!window.has_more);

        let index_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = 'idx_authorization_admin_events_participant_id'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(index_count, 1);
    }

'''
text = replace_once(
    text,
    "    #[test]\n    fn durable_lifecycle_is_atomic_and_records_only_effective_mutations() {",
    window_tests + "    #[test]\n    fn durable_lifecycle_is_atomic_and_records_only_effective_mutations() {",
    "history window tests",
)
path.write_text(text)

# CLI moves to bounded canonical reader and exposes matching cursor controls.
path = Path("src/grant_admin.rs")
text = path.read_text()
text = replace_once(
    text,
    """    History {\n        #[arg(long)]\n        db: PathBuf,\n        #[arg(long)]\n        participant_id: Option<String>,\n    },\n""",
    """    History {\n        #[arg(long)]\n        db: PathBuf,\n        #[arg(long)]\n        participant_id: Option<String>,\n        #[arg(long)]\n        before: Option<i64>,\n        #[arg(long)]\n        after: Option<i64>,\n        #[arg(long)]\n        limit: Option<usize>,\n        #[arg(long)]\n        order: Option<String>,\n    },\n""",
    "history CLI arguments",
)
old_match = """        GrantCommand::History {\n            db: path,\n            participant_id,\n        } => {\n            require_database(&path)?;\n            let conn = db::connect_read_only(&path)?;\n            if !authorization_admin::schema_current(&conn)? {\n                return Err(format!(\n                    \"authorization administration schema requires migration: run `conversation-blackboard db init --db {}` before history\",\n                    path.display()\n                )\n                .into());\n            }\n            let events =\n                authorization_admin::read_administration_events(&conn, participant_id.as_deref())?;\n            println!(\"AUTHORIZATION ADMINISTRATION HISTORY\");\n            println!(\"id\\tstore\\tgrant_id\\toperation\\tactor\\tparticipant_id\\tprincipal\\tcapability\\tresource\\tintent_id\\texpires_at\\tone_shot\\tbefore\\tafter\\tcreated_at\");\n            for event in events {\n"""
new_match = """        GrantCommand::History {\n            db: path,\n            participant_id,\n            before,\n            after,\n            limit,\n            order,\n        } => {\n            require_database(&path)?;\n            let conn = db::connect_read_only(&path)?;\n            if !authorization_admin::schema_current(&conn)? {\n                return Err(format!(\n                    \"authorization administration schema requires migration: run `conversation-blackboard db init --db {}` before history\",\n                    path.display()\n                )\n                .into());\n            }\n            let window = authorization_admin::read_administration_event_window(\n                &conn,\n                authorization_admin::AuthorizationAdministrationHistoryWindowRequest {\n                    participant_id: participant_id.as_deref(),\n                    before,\n                    after,\n                    limit,\n                    order: order.as_deref(),\n                },\n            )\n            .map_err(|_| \"invalid administration history window\")?;\n            println!(\"AUTHORIZATION ADMINISTRATION HISTORY\");\n            println!(\"order    : {}\", window.order);\n            println!(\"has_more : {}\", window.has_more);\n            println!(\"id\\tstore\\tgrant_id\\toperation\\tactor\\tparticipant_id\\tprincipal\\tcapability\\tresource\\tintent_id\\texpires_at\\tone_shot\\tbefore\\tafter\\tcreated_at\");\n            for event in window.events {\n"""
text = replace_once(text, old_match, new_match, "history CLI dispatch")
text = text.replace(
    """dispatch(GrantCommand::History {\n            db: dir.path().join(\"board.db\"),\n            participant_id: Some(\"maker-main\".to_owned()),\n        })""",
    """dispatch(GrantCommand::History {\n            db: dir.path().join(\"board.db\"),\n            participant_id: Some(\"maker-main\".to_owned()),\n            before: None,\n            after: None,\n            limit: None,\n            order: None,\n        })""",
)
text = text.replace(
    """dispatch(GrantCommand::History {\n                db: path.clone(),\n                participant_id: None,\n            })""",
    """dispatch(GrantCommand::History {\n                db: path.clone(),\n                participant_id: None,\n                before: None,\n                after: None,\n                limit: None,\n                order: None,\n            })""",
)
path.write_text(text)
