from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1))


auth = Path("src/authorization.rs")
text = auth.read_text()
anchor = "pub fn effective_grants(\n    conn: &Connection,\n    principal: &Principal,\n    participant_id: &str,\n) -> rusqlite::Result<Vec<EffectiveGrant>> {\n    ensure_grant_schema(conn)?;\n"
replacement = '''pub fn effective_grants_schema_current(conn: &Connection) -> rusqlite::Result<bool> {
    Ok(table_has_columns(
        conn,
        "principal_grants",
        &[
            "id",
            "principal_provider",
            "principal_subject",
            "participant_id",
            "capability",
            "resource",
            "status",
        ],
    )? && table_has_columns(
        conn,
        "web_participants",
        &[
            "participant_id",
            "status",
            "role",
            "owner_provider",
            "owner_subject",
        ],
    )?)
}

pub fn effective_grants(
    conn: &Connection,
    principal: &Principal,
    participant_id: &str,
) -> rusqlite::Result<Vec<EffectiveGrant>> {
    if !effective_grants_schema_current(conn)? {
        return Err(rusqlite::Error::InvalidQuery);
    }
'''
if anchor not in text:
    raise RuntimeError("effective_grants entry anchor not found")
text = text.replace(anchor, replacement, 1)
old_implicit = '''        if authorize(
            conn,
            principal,
            participant_id,
            capability,
            implicit_resource,
        )? {
'''
new_implicit = '''        if evaluate_authorization_current_schema(
            conn,
            principal,
            participant_id,
            capability,
            implicit_resource,
            None,
        )?
        .allowed
        {
'''
if old_implicit not in text:
    raise RuntimeError("effective_grants implicit authorize anchor not found")
text = text.replace(old_implicit, new_implicit, 1)
marker = "mod issue114_access_context_readonly_tests"
if marker not in text:
    text += r'''

#[cfg(test)]
mod issue114_access_context_readonly_tests {
    use super::*;

    #[test]
    fn effective_grants_rejects_stale_schema_without_repair() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                label TEXT,
                owner_provider TEXT,
                owner_subject TEXT,
                status TEXT NOT NULL,
                role TEXT NOT NULL
            );
            INSERT INTO web_participants
                (participant_id, source, owner_provider, owner_subject, status, role)
            VALUES
                ('single-main', 'single', NULL, NULL, 'active', 'user');",
        )
        .unwrap();
        assert!(!effective_grants_schema_current(&conn).unwrap());

        let principal = Principal {
            provider: "participant-hmac".to_owned(),
            subject: "single-main".to_owned(),
        };
        assert!(matches!(
            effective_grants(&conn, &principal, "single-main"),
            Err(rusqlite::Error::InvalidQuery)
        ));

        let grant_table_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'principal_grants'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(grant_table_count, 0);
    }
}
'''
auth.write_text(text)

replace_once(
    "src/access_api.rs",
    '''    let (role, grants) = with_db(&state, move |conn| {
        let role = identity::get_web_participant_role(conn, &lookup)?;
        let grants = if role.is_some() {
            authorization::effective_grants(conn, &principal_for_grants, &lookup)?
        } else {
            Vec::new()
        };
        Ok((role, grants))
    })
    .await?;

    let participant_id = role.as_ref().map(|_| resolved.instance.clone());
''',
    '''    let (schema_current, role, grants) = with_db_read_only(&state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)? {
            return Ok((false, None, Vec::new()));
        }
        let role = identity::get_web_participant_role(conn, &lookup)?;
        let grants = if role.is_some() {
            authorization::effective_grants(conn, &principal_for_grants, &lookup)?
        } else {
            Vec::new()
        };
        Ok((true, role, grants))
    })
    .await?;
    if !schema_current {
        return Err(AccessApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "authorization_schema_not_current",
        ));
    }

    let participant_id = role.as_ref().map(|_| resolved.instance.clone());
''',
)

replace_once(
    "src/mcp.rs",
    '''    let principal_for_grants = principal.clone();
    let lookup = participant_id.clone();
    let grants = match with_db(state, move |conn| {
        authorization::effective_grants(conn, &principal_for_grants, &lookup)
    })
    .await
    {
        Ok(grants) => grants,
        Err(()) => return tool_error("database_unavailable"),
    };
    if transport_principal.is_some() && grants.is_empty() {
''',
    '''    let principal_for_grants = principal.clone();
    let lookup = participant_id.clone();
    let (schema_current, grants) = match with_db_read_only(state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)? {
            return Ok((false, Vec::new()));
        }
        Ok((
            true,
            authorization::effective_grants(conn, &principal_for_grants, &lookup)?,
        ))
    })
    .await
    {
        Ok(result) => result,
        Err(()) => return tool_error("database_unavailable"),
    };
    if !schema_current {
        return tool_error("authorization_schema_not_current");
    }
    if transport_principal.is_some() && grants.is_empty() {
''',
)

http_tests = Path("src/http_contract_tests.rs")
http_text = http_tests.read_text()
http_marker = "access_context_stale_authorization_schema_is_read_only"
if http_marker not in http_text:
    http_text += r'''

#[tokio::test]
async fn access_context_stale_authorization_schema_is_read_only() {
    let fixture = fixture("access-context-stale");
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute_batch("DROP TABLE principal_grants;").unwrap();
    drop(conn);
    let before = std::fs::read(&fixture.db_path).unwrap();

    let router = fixture.router.clone().merge(crate::access_api::app(AppState {
        db_path: fixture.db_path.clone(),
        registration_key: None,
    }));
    let session = web_auth::issue_web_session(&fixture.participant_id);
    let response = request(
        &router,
        Method::GET,
        "/api/access-context",
        Some(&session.token),
        None,
    )
    .await;
    let (status, body) = response_json(response).await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body["error"], "authorization_schema_not_current");

    let after = std::fs::read(&fixture.db_path).unwrap();
    assert_eq!(before, after);
    let conn = db::connect(&fixture.db_path).unwrap();
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'principal_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(count, 0);
}
'''
http_tests.write_text(http_text)

mcp_tests = Path("src/mcp_contract_tests.rs")
mcp_text = mcp_tests.read_text()
mcp_marker = "mcp_access_context_stale_authorization_schema_is_read_only"
if mcp_marker not in mcp_text:
    mcp_text += r'''

#[tokio::test]
async fn mcp_access_context_stale_authorization_schema_is_read_only() {
    let fixture = fixture();
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute_batch("DROP TABLE principal_grants;").unwrap();
    drop(conn);
    let before = std::fs::read(&fixture.db_path).unwrap();

    let value = call_tool(
        &fixture.router,
        1140,
        "blackboard_access_context",
        capability_arguments(
            &fixture.single_secret,
            "single-main",
            "access_context",
            None,
        ),
    )
    .await;
    assert_eq!(tool_error_code(&value), "authorization_schema_not_current");

    let after = std::fs::read(&fixture.db_path).unwrap();
    assert_eq!(before, after);
    let conn = db::connect(&fixture.db_path).unwrap();
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'principal_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(count, 0);
}
'''
mcp_tests.write_text(mcp_text)

source_guard = Path("tests/access_context_readonly_contract.rs")
source_guard.write_text(r'''#[test]
fn effective_grants_cannot_reenter_schema_mutation_paths() {
    let source = include_str!("../src/authorization.rs");
    let start = source
        .find("pub fn effective_grants(\n")
        .expect("effective_grants function must exist");
    let rest = &source[start..];
    let end = rest[1..]
        .find("\npub fn ")
        .map(|offset| offset + 1)
        .unwrap_or(rest.len());
    let body = &rest[..end];

    assert!(body.contains("effective_grants_schema_current(conn)?"));
    assert!(body.contains("evaluate_authorization_current_schema("));
    assert!(!body.contains("ensure_grant_schema("));
    assert!(!body.contains("authorize("));
}
''')
