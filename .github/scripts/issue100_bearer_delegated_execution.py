from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
anchor = '''#[tokio::test]\nasync fn mcp_transport_remains_stateless_and_rejects_unknown_origins() {'''
tests = r'''#[tokio::test]
async fn bearer_delegated_one_shot_survives_failed_execution_and_replays_idempotently() {
    let fixture = fixture();
    let (verifier, token) = bearer_verifier_and_token("remote-agent-1");
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
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability,
             resource, intent_id, expires_at, one_shot)
         VALUES ('oidc:https://issuer.example', 'remote-agent-1', 'single-main', 'post_message',
                 'control-systems', 'bearer-delegated-001', unixepoch() + 3600, 1)",
        [],
    )
    .unwrap();
    drop(conn);

    let failed = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 112,
            "method": "tools/call",
            "params": {
                "name": "blackboard_write",
                "arguments": {
                    "participant_id": "single-main",
                    "channel": "control-systems",
                    "kind": "message",
                    "body": "first bearer attempt fails",
                    "reply_to": 999999,
                    "nonce": "bearer-delegated-001"
                }
            }
        }),
        &format!("Bearer {token}"),
    )
    .await;
    let (status, failed_value) = response_json(failed).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(tool_error_code(&failed_value), "reply_target_not_found");

    let conn = db::connect(&fixture.db_path).unwrap();
    let (consumed_at, receipt_count, message_count): (Option<i64>, i64, i64) = conn
        .query_row(
            "SELECT d.consumed_at,
                    (SELECT COUNT(*) FROM execution_receipts
                     WHERE participant_id = 'single-main'
                       AND intent_id = 'bearer-delegated-001'),
                    (SELECT COUNT(*) FROM messages
                     WHERE participant_id = 'single-main'
                       AND channel = 'control-systems'
                       AND body = 'first bearer attempt fails')
             FROM delegated_grants d
             WHERE d.intent_id = 'bearer-delegated-001'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .unwrap();
    assert!(consumed_at.is_none());
    assert_eq!(receipt_count, 0);
    assert_eq!(message_count, 0);
    drop(conn);

    let success_body = json!({
        "jsonrpc": "2.0",
        "id": 113,
        "method": "tools/call",
        "params": {
            "name": "blackboard_write",
            "arguments": {
                "participant_id": "single-main",
                "channel": "control-systems",
                "kind": "message",
                "body": "second bearer attempt commits",
                "nonce": "bearer-delegated-001"
            }
        }
    });
    let succeeded = request_with_authorization(
        &router,
        success_body.clone(),
        &format!("Bearer {token}"),
    )
    .await;
    let (status, succeeded_value) = response_json(succeeded).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(succeeded_value["result"]["isError"], false);
    assert_eq!(
        succeeded_value["result"]["structuredContent"]["status"],
        "created"
    );
    let message_id = succeeded_value["result"]["structuredContent"]["id"]
        .as_i64()
        .unwrap();

    let replayed = request_with_authorization(
        &router,
        success_body,
        &format!("Bearer {token}"),
    )
    .await;
    let (status, replayed_value) = response_json(replayed).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(replayed_value["result"]["isError"], false);
    assert_eq!(
        replayed_value["result"]["structuredContent"]["status"],
        "existing"
    );
    assert_eq!(
        replayed_value["result"]["structuredContent"]["id"],
        message_id
    );

    let conn = db::connect(&fixture.db_path).unwrap();
    let (consumed_at, consumed_intent, receipt_count, message_count):
        (Option<i64>, Option<String>, i64, i64) = conn
        .query_row(
            "SELECT d.consumed_at, d.consumed_intent_id,
                    (SELECT COUNT(*) FROM execution_receipts
                     WHERE participant_id = 'single-main'
                       AND intent_id = 'bearer-delegated-001'
                       AND status = 'committed'),
                    (SELECT COUNT(*) FROM messages
                     WHERE id = ?1)
             FROM delegated_grants d
             WHERE d.intent_id = 'bearer-delegated-001'",
            [message_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .unwrap();
    assert!(consumed_at.is_some());
    assert_eq!(consumed_intent.as_deref(), Some("bearer-delegated-001"));
    assert_eq!(receipt_count, 1);
    assert_eq!(message_count, 1);
}

''' + anchor
s = replace_once(s, anchor, tests, "bearer delegated execution regression")
p.write_text(s, encoding="utf-8")
