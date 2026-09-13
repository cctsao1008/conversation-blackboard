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
    conn.execute(
        "INSERT INTO execution_receipts
            (participant_id, intent_id, intent_hash, capability, message_id, status)
         VALUES ('maker-main', 'intent-85', 'hash', 'post_message', 1, 'committed')",
        [],
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
