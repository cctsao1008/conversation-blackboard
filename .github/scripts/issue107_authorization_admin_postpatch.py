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
admin_path.write_text(admin, encoding="utf-8")
