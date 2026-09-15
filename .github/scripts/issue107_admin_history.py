from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


admin_path = Path("src/authorization_admin.rs")
admin = admin_path.read_text(encoding="utf-8")

struct_anchor = """#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GrantDeactivateOutcome {
    pub rows_changed: usize,
}

"""
struct_insert = struct_anchor + """#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorizationAdministrationEvent {
    pub id: i64,
    pub grant_store: String,
    pub grant_id: i64,
    pub operation: String,
    pub actor_surface: String,
    pub actor_provider: Option<String>,
    pub actor_subject: Option<String>,
    pub actor_participant_id: Option<String>,
    pub target_principal_provider: String,
    pub target_principal_subject: String,
    pub participant_id: String,
    pub capability: String,
    pub resource: Option<String>,
    pub intent_id: Option<String>,
    pub expires_at: Option<i64>,
    pub one_shot: bool,
    pub before_status: Option<String>,
    pub after_status: String,
    pub created_at: i64,
}

"""
admin = replace_once(admin, struct_anchor, struct_insert, "administration event read model")

reader_anchor = """pub fn create_durable_grant(
"""
reader = r'''pub fn read_administration_events(
    conn: &Connection,
    participant_id: Option<&str>,
) -> rusqlite::Result<Vec<AuthorizationAdministrationEvent>> {
    if !schema_current(conn)? {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let participant_id = participant_id
        .map(|value| identity::validate_participant_id(value).ok_or(rusqlite::Error::InvalidQuery))
        .transpose()?;
    let mut query = String::from(
        "SELECT id, grant_store, grant_id, operation, actor_surface, actor_provider, actor_subject,
                actor_participant_id, target_principal_provider, target_principal_subject,
                participant_id, capability, resource, intent_id, expires_at, one_shot,
                before_status, after_status, created_at
         FROM authorization_admin_events",
    );
    if participant_id.is_some() {
        query.push_str(" WHERE participant_id = ?1");
    }
    query.push_str(" ORDER BY id");

    let mut stmt = conn.prepare(&query)?;
    let map_row = |row: &rusqlite::Row<'_>| {
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
    };
    if let Some(participant_id) = participant_id.as_deref() {
        stmt.query_map([participant_id], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>()
    } else {
        stmt.query_map([], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>()
    }
}

'''
admin = replace_once(admin, reader_anchor, reader + reader_anchor, "administration history reader")

test_anchor = """    #[test]
    fn invalid_request_creates_neither_grant_nor_event() {
"""
new_tests = r'''    #[test]
    fn administration_history_reads_effective_mutations_in_commit_order() {
        let (_dir, conn) = setup();
        let actor = AuthorizationAdministrationActor::local_cli();
        let request = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "history-agent",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: Some("history-scope"),
        };
        let created = create_durable_grant(&conn, &actor, &request).unwrap();
        create_durable_grant(&conn, &actor, &request).unwrap();
        deactivate_durable_grant(&conn, &actor, created.id).unwrap();

        let events = read_administration_events(&conn, Some("maker-main")).unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].operation, "create");
        assert_eq!(events[1].operation, "deactivate");
        assert_eq!(events[0].grant_id, created.id);
        assert_eq!(events[0].actor_surface, "local-cli");
        assert_eq!(events[0].actor_provider, None);
        assert_eq!(events[0].actor_subject, None);
        assert!(events[0].id < events[1].id);
    }

    #[test]
    fn administration_history_refuses_legacy_schema_without_mutation() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                owner_provider TEXT,
                owner_subject TEXT
            );",
        )
        .unwrap();
        let before: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(!schema_current(&conn).unwrap());
        assert!(read_administration_events(&conn, None).is_err());
        let after: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before, after);
        let admin_table: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'table' AND name = 'authorization_admin_events'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(admin_table, 0);
    }

    #[test]
    fn remote_adapters_do_not_own_authorization_mutation() {
        for (name, source) in [
            ("REST", include_str!("access_api.rs")),
            ("MCP", include_str!("mcp.rs")),
        ] {
            for forbidden in [
                "authorization_admin::create_durable_grant",
                "authorization_admin::deactivate_durable_grant",
                "authorization_admin::create_delegated_grant",
                "authorization_admin::deactivate_delegated_grant",
                "INSERT INTO principal_grants",
                "UPDATE principal_grants",
                "INSERT INTO delegated_grants",
                "UPDATE delegated_grants",
            ] {
                assert!(
                    !source.contains(forbidden),
                    "{name} adapter must not own remote authorization mutation: {forbidden}"
                );
            }
        }
    }

'''
admin = replace_once(admin, test_anchor, new_tests + test_anchor, "administration history tests")
admin_path.write_text(admin, encoding="utf-8")


grant_path = Path("src/grant_admin.rs")
grant = grant_path.read_text(encoding="utf-8")

variant_anchor = """    /// Explain a read-only authorization decision using the authoritative policy evaluator.
    Explain {
"""
history_variant = """    /// Read immutable local authorization-administration provenance.
    History {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: Option<String>,
    },
    /// Explain a read-only authorization decision using the authoritative policy evaluator.
    Explain {
"""
grant = replace_once(grant, variant_anchor, history_variant, "grant history command")

list_open_old = """            require_database(&path)?;
            let conn = db::connect(&path)?;
            let grants = list_grants(&conn, participant_id.as_deref())?;
            println!("DELEGATED GRANTS");
"""
list_open_new = """            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            if !authorization::authorization_policy_snapshot_schema_current(&conn)? {
                return Err(format!(
                    "authorization grant schema requires migration: run `conversation-blackboard db init --db {}` before list",
                    path.display()
                )
                .into());
            }
            let grants = list_grants(&conn, participant_id.as_deref())?;
            println!("DELEGATED GRANTS");
"""
grant = replace_once(grant, list_open_old, list_open_new, "delegated list read-only connection")

explain_arm = """        GrantCommand::Explain {
"""
history_arm = r'''        GrantCommand::History {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            if !authorization_admin::schema_current(&conn)? {
                return Err(format!(
                    "authorization administration schema requires migration: run `conversation-blackboard db init --db {}` before history",
                    path.display()
                )
                .into());
            }
            let events = authorization_admin::read_administration_events(
                &conn,
                participant_id.as_deref(),
            )?;
            println!("AUTHORIZATION ADMINISTRATION HISTORY");
            println!("id\tstore\tgrant_id\toperation\tactor\tparticipant_id\tprincipal\tcapability\tresource\tintent_id\texpires_at\tone_shot\tbefore\tafter\tcreated_at");
            for event in events {
                let actor = match (
                    event.actor_provider.as_deref(),
                    event.actor_subject.as_deref(),
                ) {
                    (Some(provider), Some(subject)) => format!("{provider}:{subject}"),
                    _ => event.actor_surface.clone(),
                };
                println!(
                    "{}\t{}\t{}\t{}\t{}\t{}\t{}:{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                    event.id,
                    event.grant_store,
                    event.grant_id,
                    event.operation,
                    actor,
                    event.participant_id,
                    event.target_principal_provider,
                    event.target_principal_subject,
                    event.capability,
                    event.resource.as_deref().unwrap_or("*"),
                    event.intent_id.as_deref().unwrap_or("*"),
                    event
                        .expires_at
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "never".to_owned()),
                    event.one_shot,
                    event.before_status.as_deref().unwrap_or("-"),
                    event.after_status,
                    event.created_at,
                );
            }
            Ok(())
        }
        GrantCommand::Explain {
'''
grant = replace_once(grant, explain_arm, history_arm, "grant history dispatch")

durable_list_old = """            require_database(&path)?;
            let conn = db::connect(&path)?;
            let grants = list_durable_grants(&conn, participant_id.as_deref())?;
            println!("DURABLE PRINCIPAL GRANTS");
"""
durable_list_new = """            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            if !authorization::authorization_policy_snapshot_schema_current(&conn)? {
                return Err(format!(
                    "authorization grant schema requires migration: run `conversation-blackboard db init --db {}` before durable list",
                    path.display()
                )
                .into());
            }
            let grants = list_durable_grants(&conn, participant_id.as_deref())?;
            println!("DURABLE PRINCIPAL GRANTS");
"""
grant = replace_once(grant, durable_list_old, durable_list_new, "durable list read-only connection")

grant = replace_once(
    grant,
    "    authorization::ensure_grant_schema(conn)?;\n    let mut query = String::from(\n        \"SELECT id, principal_provider, principal_subject, participant_id, capability,\n                resource, status, created_at, updated_at\n         FROM principal_grants\",\n    );",
    "    let mut query = String::from(\n        \"SELECT id, principal_provider, principal_subject, participant_id, capability,\n                resource, status, created_at, updated_at\n         FROM principal_grants\",\n    );",
    "durable list no lazy migration",
)
grant = replace_once(
    grant,
    "    authorization::ensure_grant_schema(conn)?;\n    let mut query = String::from(\n        \"SELECT id, principal_provider, principal_subject, participant_id, capability,\n                resource, intent_id, expires_at, one_shot, consumed_at, consumed_intent_id, status\n         FROM delegated_grants\",\n    );",
    "    let mut query = String::from(\n        \"SELECT id, principal_provider, principal_subject, participant_id, capability,\n                resource, intent_id, expires_at, one_shot, consumed_at, consumed_intent_id, status\n         FROM delegated_grants\",\n    );",
    "delegated list no lazy migration",
)

cli_test_anchor = """    #[test]
    fn grant_verify_accepts_clean_database() {
"""
cli_tests = r'''    #[test]
    fn grant_history_accepts_current_schema() {
        let (dir, conn) = setup();
        let spec = DurableGrantCreateSpec {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "history-agent",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: None,
        };
        create_durable_grant(&conn, &spec).unwrap();
        drop(conn);
        dispatch(GrantCommand::History {
            db: dir.path().join("board.db"),
            participant_id: Some("maker-main".to_owned()),
        })
        .unwrap();
    }

    #[test]
    fn list_and_history_refuse_legacy_schema_without_mutation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("legacy-read.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );",
        )
        .unwrap();
        let before: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        drop(conn);

        for error in [
            dispatch(GrantCommand::List {
                db: path.clone(),
                participant_id: None,
            })
            .expect_err("delegated list must refuse legacy schema")
            .to_string(),
            dispatch(GrantCommand::Durable {
                command: DurableGrantCommand::List {
                    db: path.clone(),
                    participant_id: None,
                },
            })
            .expect_err("durable list must refuse legacy schema")
            .to_string(),
            dispatch(GrantCommand::History {
                db: path.clone(),
                participant_id: None,
            })
            .expect_err("history must refuse legacy schema")
            .to_string(),
        ] {
            assert!(error.contains("schema requires migration"));
        }

        let conn = Connection::open(&path).unwrap();
        let after: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before, after);
        let policy_tables: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'table'
                   AND name IN ('principal_grants', 'delegated_grants', 'authorization_admin_events')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(policy_tables, 0);
    }

'''
grant = replace_once(grant, cli_test_anchor, cli_tests + cli_test_anchor, "history/list read-only regressions")

grant_path.write_text(grant, encoding="utf-8")
