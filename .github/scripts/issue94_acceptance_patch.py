from pathlib import Path

p = Path('src/mcp_contract_tests.rs')
s = p.read_text(encoding='utf-8')
marker = '''#[tokio::test]\nasync fn mcp_transport_remains_stateless_and_rejects_unknown_origins() {\n'''
if marker not in s:
    raise SystemExit('MCP transport test marker not found')

tests = r'''#[tokio::test]
async fn explicit_resource_scope_cannot_be_bypassed_through_mcp() {
    let fixture = fixture();
    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main', 'post_message', 'blackboard-lounge')",
        [],
    )
    .unwrap();
    drop(conn);

    let denied = call_tool(
        &fixture.router,
        50,
        "blackboard_write",
        write_arguments(
            &fixture.single_secret,
            "single-main",
            "control-systems",
            "message",
            "must be denied",
            None,
            "resource-denied-001",
        ),
    )
    .await;
    assert_eq!(tool_error_code(&denied), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    let committed: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM messages WHERE channel = 'control-systems'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(committed, 0);
}

#[tokio::test]
async fn failed_mcp_execution_does_not_burn_one_shot_delegated_grant() {
    let fixture = fixture();
    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();

    // Any explicit post_message grant suppresses participant-HMAC implicit
    // fallback for that capability. The mismatching durable grant forces this
    // request to rely on the intent-bound delegated one-shot authority.
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main', 'post_message', 'blackboard-lounge')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability,
             resource, intent_id, expires_at, one_shot)
         VALUES ('participant-hmac', 'single-main', 'single-main', 'post_message',
                 'control-systems', 'delegated-fail-001', unixepoch() + 3600, 1)",
        [],
    )
    .unwrap();
    drop(conn);

    let failed = call_tool(
        &fixture.router,
        51,
        "blackboard_write",
        write_arguments(
            &fixture.single_secret,
            "single-main",
            "control-systems",
            "message",
            "first attempt fails",
            Some(999_999),
            "delegated-fail-001",
        ),
    )
    .await;
    assert_eq!(tool_error_code(&failed), "reply_target_not_found");

    let conn = db::connect(&fixture.db_path).unwrap();
    let (consumed_at, receipt_count): (Option<i64>, i64) = conn
        .query_row(
            "SELECT d.consumed_at,
                    (SELECT COUNT(*) FROM execution_receipts
                     WHERE participant_id = 'single-main'
                       AND intent_id = 'delegated-fail-001')
             FROM delegated_grants d
             WHERE d.intent_id = 'delegated-fail-001'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap();
    assert!(consumed_at.is_none());
    assert_eq!(receipt_count, 0);
    drop(conn);

    // Reuse the same semantic intent after the failed transaction. Because the
    // first attempt committed nothing, the delegated one-shot grant is still
    // available and may be consumed by the successful execution.
    let succeeded = call_tool(
        &fixture.router,
        52,
        "blackboard_write",
        write_arguments(
            &fixture.single_secret,
            "single-main",
            "control-systems",
            "message",
            "second attempt commits",
            None,
            "delegated-fail-001",
        ),
    )
    .await;
    assert_eq!(
        succeeded["result"]["structuredContent"]["status"],
        "created"
    );

    let conn = db::connect(&fixture.db_path).unwrap();
    let (consumed_at, consumed_intent, receipt_count): (Option<i64>, Option<String>, i64) = conn
        .query_row(
            "SELECT d.consumed_at, d.consumed_intent_id,
                    (SELECT COUNT(*) FROM execution_receipts
                     WHERE participant_id = 'single-main'
                       AND intent_id = 'delegated-fail-001'
                       AND status = 'committed')
             FROM delegated_grants d
             WHERE d.intent_id = 'delegated-fail-001'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .unwrap();
    assert!(consumed_at.is_some());
    assert_eq!(consumed_intent.as_deref(), Some("delegated-fail-001"));
    assert_eq!(receipt_count, 1);
}

'''

s = s.replace(marker, tests + marker, 1)
p.write_text(s, encoding='utf-8')
