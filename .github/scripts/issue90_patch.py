from pathlib import Path

# Patch canonical verifier.
p = Path('src/execution.rs')
s = p.read_text(encoding='utf-8')
old = '''    let message_exists = conn
        .query_row(
            "SELECT 1 FROM messages WHERE id = ?1",
            [receipt.message_id],
            |_| Ok(1_i64),
        )
        .optional()?
        .is_some();
    if message_exists {
        checks.push("message_effect_present".to_owned());
    } else {
        violations.push("message_effect_missing".to_owned());
    }
'''
new = '''    let message = conn
        .query_row(
            "SELECT channel, instance, conversation_ref, kind, body, reply_to
             FROM messages WHERE id = ?1",
            [receipt.message_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, Option<i64>>(5)?,
                ))
            },
        )
        .optional()?;
    if let Some((channel, instance, conversation_ref, kind, body, reply_to)) = message {
        checks.push("message_effect_present".to_owned());
        if instance == participant_id {
            checks.push("message_effect_binding_valid".to_owned());
        } else {
            violations.push("message_effect_binding_mismatch".to_owned());
        }
        let expected_intent_hash = message_request_hash(
            &channel,
            &kind,
            &body,
            conversation_ref.as_deref(),
            reply_to,
        );
        if receipt.intent_hash == expected_intent_hash {
            checks.push("intent_hash_matches_effect".to_owned());
        } else {
            violations.push("intent_hash_mismatch".to_owned());
        }
    } else {
        violations.push("message_effect_missing".to_owned());
    }
'''
if old not in s:
    raise SystemExit('message existence block not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

# Patch integrity fixtures and add semantic tamper regressions.
p = Path('src/execution_integrity_tests.rs')
s = p.read_text(encoding='utf-8')
old = '''    conn.execute(
        "INSERT INTO execution_receipts
            (participant_id, intent_id, intent_hash, capability, message_id, status)
         VALUES ('maker-main', 'intent-85', 'hash', 'post_message', 1, 'committed')",
        [],
    )
    .unwrap();
'''
new = '''    let intent_hash = execution::message_request_hash(
        "blackboard-lounge",
        "message",
        "audit",
        None,
        None,
    );
    conn.execute(
        "INSERT INTO execution_receipts
            (participant_id, intent_id, intent_hash, capability, message_id, status)
         VALUES ('maker-main', 'intent-85', ?1, 'post_message', 1, 'committed')",
        [intent_hash],
    )
    .unwrap();
'''
if old not in s:
    raise SystemExit('seed_valid receipt block not found')
s = s.replace(old, new, 1)

old = '''        conn.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES (?1, 'shared-intent', 'hash', 'post_message', ?2, 'committed')",
            params![participant, message_id],
        )
        .unwrap();
'''
new = '''        let intent_hash = execution::message_request_hash(
            "blackboard-lounge",
            "message",
            "audit",
            None,
            None,
        );
        conn.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES (?1, 'shared-intent', ?2, 'post_message', ?3, 'committed')",
            params![participant, intent_hash, message_id],
        )
        .unwrap();
'''
if old not in s:
    raise SystemExit('shared intent receipt block not found')
s = s.replace(old, new, 1)

old = '''        conn.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES (?1, 'ambiguous-intent', 'hash', 'post_message', ?2, 'committed')",
            params![participant, message_id],
        )
        .unwrap();
'''
new = '''        let intent_hash = execution::message_request_hash(
            "blackboard-lounge",
            "message",
            "audit",
            None,
            None,
        );
        conn.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES (?1, 'ambiguous-intent', ?2, 'post_message', ?3, 'committed')",
            params![participant, intent_hash, message_id],
        )
        .unwrap();
'''
if old not in s:
    raise SystemExit('ambiguous receipt block not found')
s = s.replace(old, new, 1)

addition = r'''

#[test]
fn message_participant_binding_mismatch_is_detected() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE messages SET instance = 'other-main' WHERE id = 1",
        [],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(!report.valid);
    assert!(report
        .violations
        .iter()
        .any(|value| value == "message_effect_binding_mismatch"));
}

#[test]
fn every_hash_covered_message_field_is_verified_against_receipt() {
    let mutations = [
        "UPDATE messages SET body = 'tampered' WHERE id = 1",
        "UPDATE messages SET channel = 'other-channel' WHERE id = 1",
        "UPDATE messages SET kind = 'reply' WHERE id = 1",
        "UPDATE messages SET conversation_ref = 'changed-ref' WHERE id = 1",
        "UPDATE messages SET reply_to = 99 WHERE id = 1",
    ];
    for mutation in mutations {
        let conn = fixture();
        seed_valid(&conn);
        conn.execute(mutation, []).unwrap();
        let report =
            execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85")
                .unwrap();
        assert!(!report.valid, "mutation should invalidate audit: {mutation}");
        assert!(
            report
                .violations
                .iter()
                .any(|value| value == "intent_hash_mismatch"),
            "missing intent_hash_mismatch for mutation: {mutation}"
        );
    }
}
'''
s += addition
p.write_text(s, encoding='utf-8')

# Keep the HTTP audit contract fixture semantically valid under the stronger verifier.
p = Path('src/http_contract_tests.rs')
s = p.read_text(encoding='utf-8')
old = '''    conn.execute(
        "INSERT INTO execution_receipts
            (participant_id, intent_id, intent_hash, capability, message_id, status)
         VALUES (?1, 'intent-http-audit', 'hash', 'post_message', 1, 'committed')",
        [&fixture.participant_id],
    )
    .unwrap();
'''
new = '''    let intent_hash = execution::message_request_hash(
        "blackboard-lounge",
        "message",
        "audit seed",
        None,
        None,
    );
    conn.execute(
        "INSERT INTO execution_receipts
            (participant_id, intent_id, intent_hash, capability, message_id, status)
         VALUES (?1, 'intent-http-audit', ?2, 'post_message', 1, 'committed')",
        params![&fixture.participant_id, intent_hash],
    )
    .unwrap();
'''
if old not in s:
    raise SystemExit('HTTP audit receipt fixture block not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')
