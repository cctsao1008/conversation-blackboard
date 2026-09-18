from pathlib import Path

path = Path('src/authorization.rs')
text = path.read_text()
start = text.find('pub fn read_authorization_policy_snapshot(\n')
end = text.find('fn validate_policy_window_request(', start)
if start < 0 or end < 0 or end <= start:
    raise RuntimeError('policy snapshot/window anchors not found')
replacement = r'''pub fn read_authorization_policy_snapshot(
    conn: &Connection,
) -> rusqlite::Result<AuthorizationPolicySnapshot> {
    if !authorization_policy_snapshot_schema_current(conn)? {
        return Err(rusqlite::Error::InvalidQuery);
    }

    let mut durable_grants = Vec::new();
    let mut after = None;
    loop {
        let window = read_authorization_policy_window(
            conn,
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: None,
                after,
                limit: Some(MAX_AUTHORIZATION_POLICY_WINDOW_SIZE),
                order: Some("asc"),
            },
        )?;
        after = window.durable_grants.last().map(|grant| grant.id);
        durable_grants.extend(window.durable_grants);
        if !window.has_more {
            break;
        }
    }

    let mut delegated_grants = Vec::new();
    let mut after = None;
    loop {
        let window = read_authorization_policy_window(
            conn,
            AuthorizationPolicyWindowRequest {
                store: "delegated",
                before: None,
                after,
                limit: Some(MAX_AUTHORIZATION_POLICY_WINDOW_SIZE),
                order: Some("asc"),
            },
        )?;
        after = window.delegated_grants.last().map(|grant| grant.id);
        delegated_grants.extend(window.delegated_grants);
        if !window.has_more {
            break;
        }
    }

    Ok(AuthorizationPolicySnapshot {
        durable_grants,
        delegated_grants,
    })
}

'''
path.write_text(text[:start] + replacement + text[end:])
