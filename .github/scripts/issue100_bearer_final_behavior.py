from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
anchor = '''#[tokio::test]\nasync fn mcp_access_context_and_execution_receipt_project_shared_domain_state() {'''
tests = r'''#[tokio::test]
async fn bearer_audit_sweep_remains_explicitly_grant_controlled() {
    let fixture = fixture();
    let (verifier, token) = bearer_verifier_and_token("remote-agent-1");
    let router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );

    let call = |id: i64| {
        request_with_authorization(
            &router,
            json!({
                "jsonrpc": "2.0",
                "id": id,
                "method": "tools/call",
                "params": {
                    "name": "blackboard_execution_audit_sweep",
                    "arguments": {"participant_id": "single-main"}
                }
            }),
            &format!("Bearer {token}"),
        )
    };

    let denied = call(114).await;
    let (status, denied_value) = response_json(denied).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(tool_error_code(&denied_value), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('oidc:https://issuer.example', 'remote-agent-1', 'single-main',
                 'read_execution_audit_sweep', 'execution-audit-sweep')",
        [],
    )
    .unwrap();
    drop(conn);

    let allowed = call(115).await;
    let (status, allowed_value) = response_json(allowed).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(allowed_value["result"]["isError"], false);
    assert_eq!(
        allowed_value["result"]["structuredContent"]["sweep"]["valid"],
        true
    );
}

#[tokio::test]
async fn raw_bearer_token_never_enters_durable_sqlite_storage() {
    let fixture = fixture();
    let (verifier, token) = bearer_verifier_and_token("credential-canary-subject");
    let router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );

    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('oidc:https://issuer.example', 'credential-canary-subject', 'single-main',
                 'post_message', 'control-systems')",
        [],
    )
    .unwrap();
    drop(conn);

    let response = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 116,
            "method": "tools/call",
            "params": {
                "name": "blackboard_write",
                "arguments": {
                    "participant_id": "single-main",
                    "channel": "control-systems",
                    "kind": "message",
                    "body": "credential storage canary",
                    "nonce": "bearer-storage-canary-001"
                }
            }
        }),
        &format!("Bearer {token}"),
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(value["result"]["isError"], false);

    let needle = token.as_bytes();
    let contains_token = |bytes: &[u8]| {
        bytes
            .windows(needle.len())
            .any(|window| window == needle)
    };
    let db_bytes = std::fs::read(&fixture.db_path).unwrap();
    assert!(!contains_token(&db_bytes), "raw bearer token leaked into SQLite database");

    for suffix in ["-wal", "-journal"] {
        let sidecar = std::path::PathBuf::from(format!("{}{}", fixture.db_path.display(), suffix));
        if sidecar.exists() {
            let bytes = std::fs::read(&sidecar).unwrap();
            assert!(
                !contains_token(&bytes),
                "raw bearer token leaked into SQLite sidecar {}",
                sidecar.display()
            );
        }
    }

    let conn = db::connect(&fixture.db_path).unwrap();
    let audit = crate::execution::get_execution_audit_bundle(
        &conn,
        "single-main",
        "bearer-storage-canary-001",
    )
    .unwrap()
    .unwrap();
    let principal = audit
        .authorization
        .as_ref()
        .and_then(|authorization| authorization.principal.as_ref())
        .unwrap();
    assert_eq!(principal.provider, "oidc:https://issuer.example");
    assert_eq!(principal.subject, "credential-canary-subject");
}

''' + anchor
s = replace_once(s, anchor, tests, "final bearer behavior regressions")
p.write_text(s, encoding="utf-8")
