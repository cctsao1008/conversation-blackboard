use rusqlite::{params, Connection};

use crate::{authorization, db, execution};
use tempfile::tempdir;

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
        );
        CREATE TABLE navigation_writes (
            instance TEXT NOT NULL,
            nonce TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (instance, nonce)
        );",
    )
    .unwrap();
    execution::migrate_execution_schema(&conn).unwrap();
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
        params![&intent_hash],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)
         VALUES ('maker-main', 'intent-85', ?1, 1)",
        params![&intent_hash],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, principal_provider, principal_subject,
             capability, resource, source, reason, grant_id)
         VALUES ('maker-main', 'intent-85', 'participant-hmac', 'maker-main',
                 'post_message', 'blackboard-lounge',
                 'implicit_authority', 'implicit_participant_hmac', NULL)",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO ingress_provenance
            (delivery_id, participant_id, intent_id, transport, external_ref,
             principal_provider, principal_subject)
         VALUES ('delivery-85', 'maker-main', 'intent-85', 'rest', 'request-85',
                 'participant-hmac', 'maker-main')",
        [],
    )
    .unwrap();
}

#[test]
fn db_initialize_produces_current_execution_schema() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("board.db");
    db::initialize(&path).unwrap();
    let conn = db::connect(&path).unwrap();
    assert!(execution::execution_schema_current(&conn).unwrap());

    let ingress_columns = conn
        .prepare("PRAGMA table_info(ingress_provenance)")
        .unwrap()
        .query_map([], |row| row.get::<_, String>(1))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    assert!(ingress_columns.iter().any(|name| name == "participant_id"));

    let auth_columns = conn
        .prepare("PRAGMA table_info(execution_authorization_provenance)")
        .unwrap()
        .query_map([], |row| row.get::<_, String>(1))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    for required in [
        "principal_provider",
        "principal_subject",
        "capability",
        "resource",
        "source",
        "reason",
        "grant_id",
    ] {
        assert!(auth_columns.iter().any(|name| name == required));
    }
}

#[test]
fn execution_reads_do_not_backfill_legacy_evidence() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE ingress_provenance SET participant_id = NULL WHERE delivery_id = 'delivery-85'",
        [],
    )
    .unwrap();
    conn.execute(
        "UPDATE execution_authorization_provenance SET capability = NULL
         WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
        [],
    )
    .unwrap();
    let before = conn.total_changes();

    let _ = execution::get_execution_receipt(&conn, "maker-main", "intent-85").unwrap();
    let auth = execution::get_authorization_provenance(&conn, "maker-main", "intent-85")
        .unwrap()
        .unwrap();
    assert!(auth.capability.is_none());
    let audit = execution::get_execution_audit_bundle(&conn, "maker-main", "intent-85")
        .unwrap()
        .unwrap();
    assert!(audit.ingress.is_empty());
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(!report.valid);

    assert_eq!(conn.total_changes(), before);
    let participant: Option<String> = conn
        .query_row(
            "SELECT participant_id FROM ingress_provenance WHERE delivery_id = 'delivery-85'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert!(participant.is_none());
    let capability: Option<String> = conn
        .query_row(
            "SELECT capability FROM execution_authorization_provenance
             WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert!(capability.is_none());
}

#[test]
fn explicit_execution_migration_preserves_conservative_backfill_rules() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE ingress_provenance SET participant_id = NULL WHERE delivery_id = 'delivery-85'",
        [],
    )
    .unwrap();
    conn.execute(
        "UPDATE execution_authorization_provenance
         SET principal_provider = NULL, principal_subject = NULL,
             capability = NULL, resource = NULL
         WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
        [],
    )
    .unwrap();

    execution::migrate_execution_schema(&conn).unwrap();

    let participant: Option<String> = conn
        .query_row(
            "SELECT participant_id FROM ingress_provenance WHERE delivery_id = 'delivery-85'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(participant.as_deref(), Some("maker-main"));
    let provenance = execution::get_authorization_provenance(&conn, "maker-main", "intent-85")
        .unwrap()
        .unwrap();
    assert_eq!(provenance.capability.as_deref(), Some("post_message"));
    assert!(provenance.principal.is_none());
    assert!(provenance.resource.is_none());
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
            params![participant, &intent_hash, message_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)
             VALUES (?1, 'shared-intent', ?2, ?3)",
            params![participant, &intent_hash, message_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO execution_authorization_provenance
                (participant_id, intent_id, principal_provider, principal_subject,
                 capability, resource, source, reason, grant_id)
             VALUES (?1, 'shared-intent', 'participant-hmac', ?1,
                     'post_message', 'blackboard-lounge',
                     'implicit_authority', 'implicit_participant_hmac', NULL)",
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

    let sweep = execution::sweep_execution_audit_integrity(&conn).unwrap();
    assert!(sweep.valid, "unexpected sweep findings: {:?}", sweep);
    assert_eq!(sweep.executions_scanned, 2);
    assert!(sweep.invalid_executions.is_empty());
    assert!(sweep.orphan_evidence.is_empty());
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
    execution::migrate_execution_schema(&conn).unwrap();
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

    execution::migrate_execution_schema(&conn).unwrap();
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

#[test]
fn authorization_capability_mismatch_is_detected() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE execution_authorization_provenance SET capability = 'reply'
         WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
        [],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(!report.valid);
    assert!(report
        .violations
        .iter()
        .any(|value| value == "authorization_capability_mismatch"));
}

#[test]
fn authorization_principal_metadata_is_verified() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE execution_authorization_provenance SET principal_subject = ''
         WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
        [],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(!report.valid);
    assert!(report
        .violations
        .iter()
        .any(|value| value == "authorization_metadata_invalid"));
}

#[test]
fn legacy_authorization_backfills_only_deterministic_capability() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE execution_authorization_provenance
         SET principal_provider = NULL, principal_subject = NULL,
             capability = NULL, resource = NULL
         WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
        [],
    )
    .unwrap();

    execution::migrate_execution_schema(&conn).unwrap();
    let provenance = execution::get_authorization_provenance(&conn, "maker-main", "intent-85")
        .unwrap()
        .unwrap();
    assert_eq!(provenance.capability.as_deref(), Some("post_message"));
    assert!(provenance.principal.is_none());
    assert!(provenance.resource.is_none());

    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(!report.valid);
    assert!(report
        .violations
        .iter()
        .any(|value| value == "authorization_principal_unbound"));
    assert!(report
        .violations
        .iter()
        .any(|value| value == "authorization_scope_unbound"));
}

fn seed_delegated_one_shot(conn: &Connection) -> i64 {
    seed_valid(conn);
    authorization::ensure_grant_schema(conn).unwrap();
    conn.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource,
             intent_id, expires_at, one_shot, consumed_at, consumed_intent_id)
         VALUES ('oidc:https://issuer.example', 'agent-96', 'maker-main', 'post_message',
                 'blackboard-lounge', 'intent-85', unixepoch() + 3600, 1,
                 unixepoch(), 'intent-85')",
        [],
    )
    .unwrap();
    let grant_id = conn.last_insert_rowid();
    conn.execute(
        "UPDATE execution_authorization_provenance
         SET principal_provider = 'oidc:https://issuer.example',
             principal_subject = 'agent-96',
             source = 'delegated_grants',
             reason = 'delegated_grant_match',
             grant_id = ?1
         WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
        [grant_id],
    )
    .unwrap();
    grant_id
}

#[test]
fn navigation_reservation_integrity_is_verified() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "DELETE FROM navigation_writes WHERE instance = 'maker-main' AND nonce = 'intent-85'",
        [],
    )
    .unwrap();
    let before = conn.total_changes();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(!report.valid);
    assert!(report
        .violations
        .iter()
        .any(|value| value == "navigation_reservation_missing"));
    assert_eq!(conn.total_changes(), before);
    let remaining: i64 = conn
        .query_row("SELECT COUNT(*) FROM navigation_writes", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(remaining, 0, "verifier repaired a missing reservation");

    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE navigation_writes SET instance = 'other-main'
         WHERE instance = 'maker-main' AND nonce = 'intent-85'",
        [],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(report
        .violations
        .iter()
        .any(|value| value == "navigation_binding_mismatch"));

    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE navigation_writes SET nonce = 'other-intent'
         WHERE instance = 'maker-main' AND nonce = 'intent-85'",
        [],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(report
        .violations
        .iter()
        .any(|value| value == "navigation_binding_mismatch"));

    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE navigation_writes SET message_id = 42
         WHERE instance = 'maker-main' AND nonce = 'intent-85'",
        [],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(report
        .violations
        .iter()
        .any(|value| value == "navigation_binding_mismatch"));

    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE navigation_writes SET request_hash = 'tampered'
         WHERE instance = 'maker-main' AND nonce = 'intent-85'",
        [],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(report
        .violations
        .iter()
        .any(|value| value == "navigation_hash_mismatch"));
}

#[test]
fn delegated_one_shot_consumption_is_historical_integrity_evidence() {
    let conn = fixture();
    let grant_id = seed_delegated_one_shot(&conn);
    let before = conn.total_changes();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(
        report.valid,
        "unexpected violations: {:?}",
        report.violations
    );
    assert!(report
        .checks
        .iter()
        .any(|value| value == "delegated_grant_binding_valid"));
    assert!(report
        .checks
        .iter()
        .any(|value| value == "delegated_one_shot_consumption_valid"));
    assert_eq!(conn.total_changes(), before);

    conn.execute(
        "UPDATE delegated_grants SET consumed_at = NULL WHERE id = ?1",
        [grant_id],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(report
        .violations
        .iter()
        .any(|value| value == "delegated_one_shot_consumption_missing"));

    conn.execute(
        "UPDATE delegated_grants
         SET consumed_at = unixepoch(), consumed_intent_id = 'wrong-intent'
         WHERE id = ?1",
        [grant_id],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(report
        .violations
        .iter()
        .any(|value| value == "delegated_one_shot_consumption_mismatch"));

    conn.execute(
        "UPDATE delegated_grants
         SET consumed_intent_id = 'intent-85', participant_id = 'other-main'
         WHERE id = ?1",
        [grant_id],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(report
        .violations
        .iter()
        .any(|value| value == "delegated_grant_binding_mismatch"));

    conn.execute(
        "UPDATE delegated_grants
         SET participant_id = 'maker-main', capability = 'read_messages'
         WHERE id = ?1",
        [grant_id],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(report
        .violations
        .iter()
        .any(|value| value == "delegated_grant_binding_mismatch"));

    conn.execute(
        "UPDATE delegated_grants
         SET capability = 'post_message', status = 'inactive', expires_at = 0,
             consumed_at = unixepoch(), consumed_intent_id = 'intent-85'
         WHERE id = ?1",
        [grant_id],
    )
    .unwrap();
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(
        report.valid,
        "current grant status/expiry rewrote historical validity: {:?}",
        report.violations
    );
}

#[test]
fn clean_execution_audit_sweep_is_valid_and_read_only() {
    let conn = fixture();
    seed_valid(&conn);
    let before = conn.total_changes();

    let report = execution::sweep_execution_audit_integrity(&conn).unwrap();

    assert!(report.valid, "unexpected sweep findings: {:?}", report);
    assert_eq!(report.executions_scanned, 1);
    assert!(report.invalid_executions.is_empty());
    assert!(report.orphan_evidence.is_empty());
    assert_eq!(conn.total_changes(), before);
}

#[test]
fn execution_audit_sweep_reuses_per_execution_integrity_diagnostics() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute("UPDATE messages SET body = 'tampered' WHERE id = 1", [])
        .unwrap();
    let before = conn.total_changes();

    let report = execution::sweep_execution_audit_integrity(&conn).unwrap();

    assert!(!report.valid);
    assert_eq!(report.executions_scanned, 1);
    assert_eq!(report.invalid_executions.len(), 1);
    let invalid = &report.invalid_executions[0];
    assert_eq!(invalid.participant_id, "maker-main");
    assert_eq!(invalid.intent_id, "intent-85");
    assert!(invalid
        .violations
        .iter()
        .any(|value| value == "intent_hash_mismatch"));
    assert_eq!(conn.total_changes(), before);
}

#[test]
fn execution_audit_sweep_detects_orphan_and_unbound_evidence() {
    let conn = fixture();
    seed_valid(&conn);

    conn.execute(
        "INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, source, reason)
         VALUES ('ghost-main', 'orphan-auth', 'implicit_authority', 'test')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO ingress_provenance
            (delivery_id, participant_id, intent_id, transport, external_ref,
             principal_provider, principal_subject)
         VALUES ('delivery-orphan', 'ghost-main', 'orphan-ingress', 'rest', 'request-orphan',
                 'participant-hmac', 'ghost-main')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO ingress_provenance
            (delivery_id, participant_id, intent_id, transport, external_ref,
             principal_provider, principal_subject)
         VALUES ('delivery-legacy', NULL, 'legacy-intent', 'rest', 'legacy',
                 'participant-hmac', 'unknown')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)
         VALUES ('ghost-main', 'orphan-navigation', 'hash', 99)",
        [],
    )
    .unwrap();
    let before = conn.total_changes();

    let report = execution::sweep_execution_audit_integrity(&conn).unwrap();

    assert!(!report.valid);
    for expected in [
        "orphan_authorization_provenance",
        "orphan_ingress_provenance",
        "orphan_navigation_reservation",
        "unbound_legacy_ingress",
    ] {
        assert!(
            report
                .orphan_evidence
                .iter()
                .any(|evidence| evidence.kind == expected),
            "missing orphan diagnostic: {expected}; findings={:?}",
            report.orphan_evidence
        );
    }
    let legacy = report
        .orphan_evidence
        .iter()
        .find(|evidence| evidence.kind == "unbound_legacy_ingress")
        .unwrap();
    assert!(legacy.participant_id.is_none());
    assert_eq!(legacy.intent_id, "legacy-intent");
    assert_eq!(conn.total_changes(), before);
}
