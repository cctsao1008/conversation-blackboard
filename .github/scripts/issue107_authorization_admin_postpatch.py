from pathlib import Path


def replace_exact(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} matches, found {count}")
    return text.replace(old, new)


grant_path = Path("src/grant_admin.rs")
grant = grant_path.read_text(encoding="utf-8")
grant = replace_exact(
    grant,
    "fn normalize(fn normalize(",
    "fn normalize(",
    1,
    "repair removed CLI participant validator anchor",
)
grant = replace_exact(
    grant,
    "use rusqlite::{params, Connection, OptionalExtension};",
    "use rusqlite::Connection;",
    1,
    "remove mutation-only rusqlite imports from CLI adapter",
)
grant_path.write_text(grant, encoding="utf-8")

admin_path = Path("src/authorization_admin.rs")
admin = admin_path.read_text(encoding="utf-8")
admin = replace_exact(
    admin,
    "    migrate_schema(conn)?;\n",
    "    require_schema_current(conn)?;\n",
    4,
    "mutation services must not migrate schema",
)

anchor = """    )
}

pub fn create_durable_grant(
"""
insert = """    )
}

pub fn schema_current(conn: &Connection) -> rusqlite::Result<bool> {
    Ok(authorization::authorization_policy_snapshot_schema_current(conn)?
        && table_has_columns(conn, "delegated_grants", &["updated_at"])?
        && table_has_columns(
            conn,
            "authorization_admin_events",
            &[
                "id",
                "grant_store",
                "grant_id",
                "operation",
                "actor_surface",
                "actor_provider",
                "actor_subject",
                "actor_participant_id",
                "target_principal_provider",
                "target_principal_subject",
                "participant_id",
                "capability",
                "resource",
                "intent_id",
                "expires_at",
                "one_shot",
                "before_status",
                "after_status",
                "created_at",
            ],
        )?)
}

fn require_schema_current(conn: &Connection) -> AdministrationResult<()> {
    if !schema_current(conn)? {
        return Err("authorization administration schema requires migration: run db init first".into());
    }
    Ok(())
}

fn table_has_columns(
    conn: &Connection,
    table: &str,
    required: &[&str],
) -> rusqlite::Result<bool> {
    let exists: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?1",
        [table],
        |row| row.get(0),
    )?;
    if exists == 0 {
        return Ok(false);
    }
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let columns = stmt
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(required
        .iter()
        .all(|required| columns.iter().any(|column| column == required)))
}

pub fn create_durable_grant(
"""
admin = replace_exact(
    admin,
    anchor,
    insert,
    1,
    "insert administration schema-current boundary",
)
admin = replace_exact(
    admin,
    "        stmt.query_map(\n",
    "        let rows = stmt.query_map(\n",
    2,
    "materialize rusqlite mapped rows before statement drop",
)
admin = replace_exact(
    admin,
    "        .collect::<rusqlite::Result<Vec<_>>>()?\n    };",
    "        .collect::<rusqlite::Result<Vec<_>>>()?;\n        rows\n    };",
    2,
    "finish rusqlite row materialization inside block",
)

state_anchor = """struct AdministrationEvent<'a> {
    grant_store: &'a str,
    grant_id: i64,
    operation: &'a str,
    scope: GrantScope<'a>,
    before_status: Option<&'a str>,
    after_status: &'a str,
}

"""
state_insert = state_anchor + """#[derive(Debug)]
struct DelegatedGrantState {
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
    intent_id: Option<String>,
    expires_at: Option<i64>,
    one_shot: bool,
    status: String,
}

"""
admin = replace_exact(
    admin,
    state_anchor,
    state_insert,
    1,
    "add named delegated grant lifecycle row",
)

old_scope = """    let scope: Option<(
        String,
        String,
        String,
        String,
        Option<String>,
        Option<String>,
        Option<i64>,
        bool,
        String,
    )> = tx
        .query_row(
            "SELECT principal_provider, principal_subject, participant_id, capability,
                    resource, intent_id, expires_at, one_shot, status
             FROM delegated_grants WHERE id = ?1",
            [grant_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    row.get(6)?,
                    row.get::<_, i64>(7)? != 0,
                    row.get(8)?,
                ))
            },
        )
        .optional()?;
    let Some((provider, subject, participant_id, capability, resource, intent_id, expires_at, one_shot, status)) = scope else {
        tx.commit()?;
        return Ok(None);
    };
"""
new_scope = """    let scope: Option<DelegatedGrantState> = tx
        .query_row(
            "SELECT principal_provider, principal_subject, participant_id, capability,
                    resource, intent_id, expires_at, one_shot, status
             FROM delegated_grants WHERE id = ?1",
            [grant_id],
            |row| {
                Ok(DelegatedGrantState {
                    principal_provider: row.get(0)?,
                    principal_subject: row.get(1)?,
                    participant_id: row.get(2)?,
                    capability: row.get(3)?,
                    resource: row.get(4)?,
                    intent_id: row.get(5)?,
                    expires_at: row.get(6)?,
                    one_shot: row.get::<_, i64>(7)? != 0,
                    status: row.get(8)?,
                })
            },
        )
        .optional()?;
    let Some(scope) = scope else {
        tx.commit()?;
        return Ok(None);
    };
"""
admin = replace_exact(
    admin,
    old_scope,
    new_scope,
    1,
    "factor delegated grant query state",
)
admin = replace_exact(
    admin,
    "                    principal_provider: &provider,\n                    principal_subject: &subject,\n                    participant_id: &participant_id,\n                    capability: &capability,\n                    resource: resource.as_deref(),\n                    intent_id: intent_id.as_deref(),\n                    expires_at,\n                    one_shot,\n                },\n                before_status: Some(&status),",
    "                    principal_provider: &scope.principal_provider,\n                    principal_subject: &scope.principal_subject,\n                    participant_id: &scope.participant_id,\n                    capability: &scope.capability,\n                    resource: scope.resource.as_deref(),\n                    intent_id: scope.intent_id.as_deref(),\n                    expires_at: scope.expires_at,\n                    one_shot: scope.one_shot,\n                },\n                before_status: Some(&scope.status),",
    1,
    "use named delegated grant state in provenance",
)
admin_path.write_text(admin, encoding="utf-8")
