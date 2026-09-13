use rusqlite::{params, Connection};

use crate::execution;

fn fixture() -> Connection {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(
        "CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            source TEXT NOT NULL,
            instance TEXT NOT NULL,
            conversation_ref TEXT,
            kind TEXT NOT NULL,
            body TEXT NOT NULL,
            reply_to INTEGER
        );",
    )
    .unwrap();
    execution::ensure_execution_tables(&conn).unwrap();
    conn
}

fn seed_valid(conn: &Connection) {
    conn.execute(
        "INSERT INTO messages
            (id, channel, source, instance, kind, body)
         VALUES (1, 'blackboard-lounge', 'test', 'maker-main', 'message', 'audit')",
        [],
    )
    .unwrap();
    let intent_hash =
        execution::message_request_hash("blackboard-lounge", "message", "audit", None, None);
    conn.execute(
        "INSERT INTO execution_receipts
            (participant_id, intent_id, intent_hash, capability, message_id, status)
         VALUES ('maker-main', 'intent-85', ?1, 'post_message', 1, 'committed')",
        [intent_hash],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, source, reason, grant_id)
         VALUES ('maker-main', 'intent-85', 'implicit_authority', 'implicit_participant_hmac', NULL)",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO ingress_provenance
            (delivery_id, intent_id, transport, external_ref, principal_provider, principal_subject)
         VALUES ('delivery-85', 'intent-85', 'rest', 'request-85', 'participant-hmac', 'maker-main')",
        [],
    )
    .unwrap();
}

#[test]
fn valid_committed_audit_passes_integrity_verification() {
    let conn = fixture();
    seed_valid(&conn);
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(report.valid);
    assert!(report.violations.is_empty());
    assert!(report.checks.iter().any(|v| v == "valid_committed_audit"));
}

#[test]
fn missing_receipt_is_reported_without_policy_re_evaluation() {
    let conn = fixture();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "missing").unwrap();
    assert!(!report.valid);
    assert_eq!(report.violations, vec!["receipt_missing"]);
}

#[test]
fn missing_message_and_authorization_are_detected() {
    let conn = fixture();
    conn.execute(
        "INSERT INTO execution_receipts
            (participant_id, intent_id, intent_hash, capability, message_id, status)
         VALUES ('maker-main', 'intent-broken', 'hash', 'post_message', 99, 'committed')",
        [],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-broken").unwrap();
    assert!(!report.valid);
    assert!(report
        .violations
        .iter()
        .any(|v| v == "message_effect_missing"));
    assert!(report
        .violations
        .iter()
        .any(|v| v == "authorization_provenance_missing"));
}

#[test]
fn invalid_status_and_ingress_metadata_are_detected() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE execution_receipts SET status = 'pending'
         WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
        [],
    )
    .unwrap();
    conn.execute(
        "UPDATE ingress_provenance SET transport = '' WHERE delivery_id = 'delivery-85'",
        [],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(!report.valid);
    assert!(report
        .violations
        .iter()
        .any(|v| v == "invalid_receipt_status"));
    assert!(report
        .violations
        .iter()
        .any(|v| v == "ingress_metadata_invalid"));
}

#[test]
fn verifier_is_read_only() {
    let conn = fixture();
    seed_valid(&conn);
    let before: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM execution_receipts WHERE intent_id = ?1",
            params!["intent-85"],
            |row| row.get(0),
        )
        .unwrap();
    let _ = execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    let after: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM execution_receipts WHERE intent_id = ?1",
            params!["intent-85"],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(before, after);
}

#[test]
fn same_intent_across_participants_keeps_ingress_separate() {
    let conn = fixture();
    for (message_id, participant) in [(1_i64, "alpha-main"), (2_i64, "beta-main")] {
        conn.execute(
            "INSERT INTO messages (id, channel, source, instance, kind, body)
             VALUES (?1, 'blackboard-lounge', 'test', ?2, 'message', 'audit')",
            params![message_id, participant],
        )
        .unwrap();
        let intent_hash =
            execution::message_request_hash("blackboard-lounge", "message", "audit", None, None);
        conn.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES (?1, 'shared-intent', ?2, 'post_message', ?3, 'committed')",
            params![participant, intent_hash, message_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO execution_authorization_provenance
                (participant_id, intent_id, source, reason, grant_id)
             VALUES (?1, 'shared-intent', 'implicit_authority', 'implicit_participant_hmac', NULL)",
            [participant],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO ingress_provenance
                (delivery_id, participant_id, intent_id, transport, external_ref,
                 principal_provider, principal_subject)
             VALUES (?1, ?2, 'shared-intent', 'rest', ?1, 'participant-hmac', ?2)",
            params![format!("delivery-{participant}"), participant],
        )
        .unwrap();
    }

    let alpha = execution::get_execution_audit_bundle(&conn, "alpha-main", "shared-intent")
        .unwrap()
        .unwrap();
    let beta = execution::get_execution_audit_bundle(&conn, "beta-main", "shared-intent")
        .unwrap()
        .unwrap();
    assert_eq!(alpha.ingress.len(), 1);
    assert_eq!(beta.ingress.len(), 1);
    assert_eq!(alpha.ingress[0].participant_id, "alpha-main");
    assert_eq!(beta.ingress[0].participant_id, "beta-main");
    assert_ne!(alpha.ingress[0].delivery_id, beta.ingress[0].delivery_id);

    assert!(
        execution::verify_execution_audit_integrity(&conn, "alpha-main", "shared-intent")
            .unwrap()
            .valid
    );
    assert!(
        execution::verify_execution_audit_integrity(&conn, "beta-main", "shared-intent")
            .unwrap()
            .valid
    );
}

#[test]
fn unambiguous_legacy_ingress_is_backfilled() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE ingress_provenance SET participant_id = NULL WHERE delivery_id = 'delivery-85'",
        [],
    )
    .unwrap();
    execution::ensure_execution_tables(&conn).unwrap();
    let participant: Option<String> = conn
        .query_row(
            "SELECT participant_id FROM ingress_provenance WHERE delivery_id = 'delivery-85'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(participant.as_deref(), Some("maker-main"));
}

#[test]
fn ambiguous_legacy_ingress_is_not_guessed() {
    let conn = fixture();
    for (message_id, participant) in [(1_i64, "alpha-main"), (2_i64, "beta-main")] {
        conn.execute(
            "INSERT INTO messages (id, channel, source, instance, kind, body)
             VALUES (?1, 'blackboard-lounge', 'test', ?2, 'message', 'audit')",
            params![message_id, participant],
        )
        .unwrap();
        let intent_hash =
            execution::message_request_hash("blackboard-lounge", "message", "audit", None, None);
        conn.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES (?1, 'ambiguous-intent', ?2, 'post_message', ?3, 'committed')",
            params![participant, intent_hash, message_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO execution_authorization_provenance
                (participant_id, intent_id, source, reason, grant_id)
             VALUES (?1, 'ambiguous-intent', 'implicit_authority', 'implicit_participant_hmac', NULL)",
            [participant],
        )
        .unwrap();
    }
    conn.execute(
        "INSERT INTO ingress_provenance
            (delivery_id, participant_id, intent_id, transport, external_ref,
             principal_provider, principal_subject)
         VALUES ('legacy-ambiguous', NULL, 'ambiguous-intent', 'rest', 'legacy',
                 'participant-hmac', 'unknown')",
        [],
    )
    .unwrap();

    execution::ensure_execution_tables(&conn).unwrap();
    let participant: Option<String> = conn
        .query_row(
            "SELECT participant_id FROM ingress_provenance WHERE delivery_id = 'legacy-ambiguous'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert!(participant.is_none());

    let alpha = execution::get_execution_audit_bundle(&conn, "alpha-main", "ambiguous-intent")
        .unwrap()
        .unwrap();
    assert!(alpha.ingress.is_empty());
    let report =
        execution::verify_execution_audit_integrity(&conn, "alpha-main", "ambiguous-intent")
            .unwrap();
    assert!(!report.valid);
    assert!(report
        .violations
        .iter()
        .any(|value| value == "ingress_participant_unbound"));
}

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
            execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
        assert!(
            !report.valid,
            "mutation should invalidate audit: {mutation}"
        );
        assert!(
            report
                .violations
                .iter()
                .any(|value| value == "intent_hash_mismatch"),
            "missing intent_hash_mismatch for mutation: {mutation}"
        );
    }
}
