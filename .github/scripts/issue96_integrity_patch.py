from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing marker: {label}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Canonical verifier: cover navigation reservation + delegated one-shot state.
# ---------------------------------------------------------------------------
p = Path("src/execution.rs")
s = p.read_text(encoding="utf-8")

authorization_marker = '''    let authorization_count: i64 = conn.query_row(\n        "SELECT COUNT(*) FROM execution_authorization_provenance\n'''

navigation_block = r'''    let navigation_table_exists: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master
         WHERE type = 'table' AND name = 'navigation_writes'",
        [],
        |row| row.get(0),
    )?;
    if navigation_table_exists == 0 {
        violations.push("navigation_reservation_missing".to_owned());
    } else {
        let exact_navigation: Option<(String, String, String, i64)> = conn
            .query_row(
                "SELECT instance, nonce, request_hash, message_id
                 FROM navigation_writes
                 WHERE instance = ?1 AND nonce = ?2
                 LIMIT 1",
                params![participant_id, intent_id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .optional()?;
        let navigation = match exact_navigation {
            Some(row) => Some(row),
            None => conn
                .query_row(
                    "SELECT instance, nonce, request_hash, message_id
                     FROM navigation_writes
                     WHERE nonce = ?1 OR message_id = ?2
                     ORDER BY CASE WHEN nonce = ?1 THEN 0 ELSE 1 END, created_at
                     LIMIT 1",
                    params![intent_id, receipt.message_id],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
                )
                .optional()?,
        };

        match navigation {
            None => violations.push("navigation_reservation_missing".to_owned()),
            Some((instance, nonce, request_hash, message_id)) => {
                checks.push("navigation_reservation_present".to_owned());
                if instance == receipt.participant_id
                    && nonce == receipt.intent_id
                    && message_id == receipt.message_id
                {
                    checks.push("navigation_binding_valid".to_owned());
                } else {
                    violations.push("navigation_binding_mismatch".to_owned());
                }
                if request_hash == receipt.intent_hash {
                    checks.push("navigation_hash_matches_receipt".to_owned());
                } else {
                    violations.push("navigation_hash_mismatch".to_owned());
                }
            }
        }
    }

'''
s = replace_once(
    s,
    authorization_marker,
    navigation_block + authorization_marker,
    "navigation verification insertion",
)

# This marker is deliberately verifier-specific. The audit-bundle reader has a
# similar ingress SELECT, but it scopes by participant + intent. The verifier's
# ingress scan scopes by intent only so it can diagnose unresolved legacy rows.
verifier_ingress_marker = '''        _ => violations.push("authorization_provenance_duplicate".to_owned()),\n    }\n\n    let mut stmt = conn.prepare(\n        "SELECT participant_id, delivery_id, intent_id, transport, external_ref,\n                principal_provider, principal_subject\n         FROM ingress_provenance\n         WHERE intent_id = ?1\n'''

delegated_block = r'''        _ => violations.push("authorization_provenance_duplicate".to_owned()),
    }

    if let Some(auth) = get_authorization_provenance(conn, participant_id, intent_id)? {
        if auth.source == "delegated_grants" {
            match auth.grant_id {
                None => violations.push("delegated_grant_reference_missing".to_owned()),
                Some(grant_id) => {
                    let delegated_table_exists: i64 = conn.query_row(
                        "SELECT COUNT(*) FROM sqlite_master
                         WHERE type = 'table' AND name = 'delegated_grants'",
                        [],
                        |row| row.get(0),
                    )?;
                    if delegated_table_exists == 0 {
                        violations.push("delegated_grant_missing".to_owned());
                    } else {
                        let grant: Option<(
                            String,
                            String,
                            i64,
                            Option<i64>,
                            Option<String>,
                        )> = conn
                            .query_row(
                                "SELECT participant_id, capability, one_shot,
                                        consumed_at, consumed_intent_id
                                 FROM delegated_grants
                                 WHERE id = ?1",
                                [grant_id],
                                |row| {
                                    Ok((
                                        row.get(0)?,
                                        row.get(1)?,
                                        row.get(2)?,
                                        row.get(3)?,
                                        row.get(4)?,
                                    ))
                                },
                            )
                            .optional()?;
                        match grant {
                            None => violations.push("delegated_grant_missing".to_owned()),
                            Some((
                                grant_participant,
                                grant_capability,
                                one_shot,
                                consumed_at,
                                consumed_intent,
                            )) => {
                                checks.push("delegated_grant_present".to_owned());
                                if grant_participant == receipt.participant_id
                                    && grant_capability == receipt.capability
                                {
                                    checks.push("delegated_grant_binding_valid".to_owned());
                                } else {
                                    violations.push("delegated_grant_binding_mismatch".to_owned());
                                }

                                if one_shot != 0 {
                                    if consumed_at.is_none() {
                                        violations.push(
                                            "delegated_one_shot_consumption_missing".to_owned(),
                                        );
                                    } else if consumed_intent.as_deref()
                                        != Some(receipt.intent_id.as_str())
                                    {
                                        violations.push(
                                            "delegated_one_shot_consumption_mismatch".to_owned(),
                                        );
                                    } else {
                                        checks.push(
                                            "delegated_one_shot_consumption_valid".to_owned(),
                                        );
                                    }
                                } else {
                                    checks.push("delegated_grant_non_one_shot".to_owned());
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    let mut stmt = conn.prepare(
        "SELECT participant_id, delivery_id, intent_id, transport, external_ref,
                principal_provider, principal_subject
         FROM ingress_provenance
         WHERE intent_id = ?1
'''
s = replace_once(
    s,
    verifier_ingress_marker,
    delegated_block,
    "verifier-scoped delegated verification insertion",
)
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# Canonical integrity tests: current fixtures include all atomic components.
# ---------------------------------------------------------------------------
p = Path("src/execution_integrity_tests.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "use crate::{db, execution};",
    "use crate::{authorization, db, execution};",
    "authorization test import",
)

fixture_old = '''        "CREATE TABLE messages (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            channel TEXT NOT NULL,\n            source TEXT NOT NULL,\n            instance TEXT NOT NULL,\n            conversation_ref TEXT,\n            kind TEXT NOT NULL,\n            body TEXT NOT NULL,\n            reply_to INTEGER\n        );",\n'''
fixture_new = '''        "CREATE TABLE messages (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            channel TEXT NOT NULL,\n            source TEXT NOT NULL,\n            instance TEXT NOT NULL,\n            conversation_ref TEXT,\n            kind TEXT NOT NULL,\n            body TEXT NOT NULL,\n            reply_to INTEGER\n        );\n        CREATE TABLE navigation_writes (\n            instance TEXT NOT NULL,\n            nonce TEXT NOT NULL,\n            request_hash TEXT NOT NULL,\n            message_id INTEGER NOT NULL,\n            created_at INTEGER NOT NULL DEFAULT (unixepoch()),\n            PRIMARY KEY (instance, nonce)\n        );",\n'''
s = replace_once(s, fixture_old, fixture_new, "navigation test table")

seed_receipt_old = '''    conn.execute(\n        "INSERT INTO execution_receipts\n            (participant_id, intent_id, intent_hash, capability, message_id, status)\n         VALUES ('maker-main', 'intent-85', ?1, 'post_message', 1, 'committed')",\n        [intent_hash],\n    )\n    .unwrap();\n'''
seed_receipt_new = '''    conn.execute(\n        "INSERT INTO execution_receipts\n            (participant_id, intent_id, intent_hash, capability, message_id, status)\n         VALUES ('maker-main', 'intent-85', ?1, 'post_message', 1, 'committed')",\n        params![&intent_hash],\n    )\n    .unwrap();\n    conn.execute(\n        "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)\n         VALUES ('maker-main', 'intent-85', ?1, 1)",\n        params![&intent_hash],\n    )\n    .unwrap();\n'''
s = replace_once(s, seed_receipt_old, seed_receipt_new, "seed valid navigation reservation")

shared_receipt_old = '''        conn.execute(\n            "INSERT INTO execution_receipts\n                (participant_id, intent_id, intent_hash, capability, message_id, status)\n             VALUES (?1, 'shared-intent', ?2, 'post_message', ?3, 'committed')",\n            params![participant, intent_hash, message_id],\n        )\n        .unwrap();\n'''
shared_receipt_new = '''        conn.execute(\n            "INSERT INTO execution_receipts\n                (participant_id, intent_id, intent_hash, capability, message_id, status)\n             VALUES (?1, 'shared-intent', ?2, 'post_message', ?3, 'committed')",\n            params![participant, &intent_hash, message_id],\n        )\n        .unwrap();\n        conn.execute(\n            "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)\n             VALUES (?1, 'shared-intent', ?2, ?3)",\n            params![participant, &intent_hash, message_id],\n        )\n        .unwrap();\n'''
s = replace_once(s, shared_receipt_old, shared_receipt_new, "shared intent navigation reservations")

extra_tests = r'''

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
        .query_row("SELECT COUNT(*) FROM navigation_writes", [], |row| row.get(0))
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
    assert!(report.valid, "unexpected violations: {:?}", report.violations);
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
'''
if "fn delegated_one_shot_consumption_is_historical_integrity_evidence" in s:
    raise SystemExit("issue96 tests already present")
s = s.rstrip() + extra_tests + "\n"
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# REST manual audit fixture must represent the full atomic execution boundary.
# ---------------------------------------------------------------------------
p = Path("src/http_contract_tests.rs")
s = p.read_text(encoding="utf-8")
http_receipt_old = '''    conn.execute(\n        "INSERT INTO execution_receipts\n            (participant_id, intent_id, intent_hash, capability, message_id, status)\n         VALUES (?1, 'intent-http-audit', ?2, 'post_message', 1, 'committed')",\n        rusqlite::params![&fixture.participant_id, intent_hash],\n    )\n    .unwrap();\n'''
http_receipt_new = '''    conn.execute(\n        "INSERT INTO execution_receipts\n            (participant_id, intent_id, intent_hash, capability, message_id, status)\n         VALUES (?1, 'intent-http-audit', ?2, 'post_message', 1, 'committed')",\n        rusqlite::params![&fixture.participant_id, &intent_hash],\n    )\n    .unwrap();\n    conn.execute(\n        "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)\n         VALUES (?1, 'intent-http-audit', ?2, 1)",\n        rusqlite::params![&fixture.participant_id, &intent_hash],\n    )\n    .unwrap();\n'''
s = replace_once(s, http_receipt_old, http_receipt_new, "HTTP audit navigation fixture")
p.write_text(s, encoding="utf-8")
