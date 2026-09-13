from pathlib import Path

# --- execution.rs: canonical integrity report + verifier ---
p = Path('src/execution.rs')
s = p.read_text(encoding='utf-8')

bundle = '''#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionAuditBundle {
    pub receipt: ExecutionReceipt,
    pub authorization: Option<AuthorizationProvenance>,
    pub ingress: Vec<IngressAuditRecord>,
}
'''
insert = bundle + '''
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionAuditIntegrityReport {
    pub participant_id: String,
    pub intent_id: String,
    pub valid: bool,
    pub checks: Vec<String>,
    pub violations: Vec<String>,
}
'''
if 'pub struct ExecutionAuditIntegrityReport' not in s:
    if bundle not in s:
        raise SystemExit('ExecutionAuditBundle marker not found')
    s = s.replace(bundle, insert, 1)

marker = '''pub fn execute_message_intent(
'''
verifier = '''pub fn verify_execution_audit_integrity(
    conn: &Connection,
    participant_id: &str,
    intent_id: &str,
) -> rusqlite::Result<ExecutionAuditIntegrityReport> {
    ensure_execution_tables(conn)?;
    let mut checks = Vec::new();
    let mut violations = Vec::new();

    let receipt = get_execution_receipt(conn, participant_id, intent_id)?;
    let Some(receipt) = receipt else {
        violations.push("receipt_missing".to_owned());
        return Ok(ExecutionAuditIntegrityReport {
            participant_id: participant_id.to_owned(),
            intent_id: intent_id.to_owned(),
            valid: false,
            checks,
            violations,
        });
    };
    checks.push("receipt_present".to_owned());

    if receipt.participant_id != participant_id || receipt.intent_id != intent_id {
        violations.push("receipt_binding_mismatch".to_owned());
    }
    if receipt.status == "committed" {
        checks.push("receipt_status_committed".to_owned());
    } else {
        violations.push("invalid_receipt_status".to_owned());
    }

    let message_exists = conn
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

    let authorization_count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM execution_authorization_provenance
         WHERE participant_id = ?1 AND intent_id = ?2",
        params![participant_id, intent_id],
        |row| row.get(0),
    )?;
    match authorization_count {
        0 => violations.push("authorization_provenance_missing".to_owned()),
        1 => {
            checks.push("authorization_provenance_present".to_owned());
            if let Some(auth) = get_authorization_provenance(conn, participant_id, intent_id)? {
                if auth.participant_id != participant_id || auth.intent_id != intent_id {
                    violations.push("authorization_binding_mismatch".to_owned());
                }
            }
        }
        _ => violations.push("authorization_provenance_duplicate".to_owned()),
    }

    let mut stmt = conn.prepare(
        "SELECT delivery_id, intent_id, transport, external_ref,
                principal_provider, principal_subject
         FROM ingress_provenance
         WHERE intent_id = ?1
         ORDER BY created_at, delivery_id",
    )?;
    let ingress = stmt
        .query_map([intent_id], |row| {
            Ok(IngressAuditRecord {
                delivery_id: row.get(0)?,
                intent_id: row.get(1)?,
                transport: row.get(2)?,
                external_ref: row.get(3)?,
                principal: Principal {
                    provider: row.get(4)?,
                    subject: row.get(5)?,
                },
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;

    let mut ingress_metadata_invalid = false;
    let mut ingress_binding_mismatch = false;
    for row in &ingress {
        if row.intent_id != intent_id {
            ingress_binding_mismatch = true;
        }
        if row.delivery_id.trim().is_empty()
            || row.transport.trim().is_empty()
            || row.external_ref.trim().is_empty()
            || row.principal.provider.trim().is_empty()
            || row.principal.subject.trim().is_empty()
        {
            ingress_metadata_invalid = true;
        }
    }
    if ingress_binding_mismatch {
        violations.push("ingress_binding_mismatch".to_owned());
    } else {
        checks.push("ingress_binding_valid".to_owned());
    }
    if ingress_metadata_invalid {
        violations.push("ingress_metadata_invalid".to_owned());
    } else {
        checks.push("ingress_metadata_valid".to_owned());
    }

    let valid = violations.is_empty();
    if valid {
        checks.push("valid_committed_audit".to_owned());
    }
    Ok(ExecutionAuditIntegrityReport {
        participant_id: participant_id.to_owned(),
        intent_id: intent_id.to_owned(),
        valid,
        checks,
        violations,
    })
}

'''
if 'pub fn verify_execution_audit_integrity(' not in s:
    if marker not in s:
        raise SystemExit('execute_message_intent marker not found')
    s = s.replace(marker, verifier + marker, 1)

p.write_text(s, encoding='utf-8')

# --- execution_audit.rs: CLI surface ---
p = Path('src/execution_audit.rs')
s = p.read_text(encoding='utf-8')
old_variant = '''    Audit {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        intent_id: String,
    },
'''
new_variant = old_variant + '''    /// Verify structural integrity of committed execution evidence without repairing it.
    VerifyAudit {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        intent_id: String,
    },
'''
if 'VerifyAudit {' not in s:
    if old_variant not in s:
        raise SystemExit('ExecutionCommand::Audit marker not found')
    s = s.replace(old_variant, new_variant, 1)

match_end = '''            Ok(())
        }
    }
}
'''
verify_arm = '''            Ok(())
        }
        ExecutionCommand::VerifyAudit {
            db: path,
            participant_id,
            intent_id,
        } => {
            require_database(&path)?;
            let participant_id = identity::validate_participant_id(&participant_id)
                .ok_or("invalid participant_id")?;
            let intent_id =
                execution::normalize_intent_id(&intent_id).map_err(|_| "invalid intent_id")?;
            let conn = db::connect(&path)?;
            let report = execution::verify_execution_audit_integrity(
                &conn,
                &participant_id,
                &intent_id,
            )?;
            println!("EXECUTION AUDIT INTEGRITY");
            println!("participant_id : {}", report.participant_id);
            println!("intent_id      : {}", report.intent_id);
            println!("valid          : {}", report.valid);
            for check in report.checks {
                println!("check          : {check}");
            }
            for violation in report.violations {
                println!("violation      : {violation}");
            }
            if report.valid {
                Ok(())
            } else {
                Err("execution audit integrity verification failed".into())
            }
        }
    }
}
'''
if 'ExecutionCommand::VerifyAudit {' not in s:
    if match_end not in s:
        raise SystemExit('dispatch match end marker not found')
    s = s.replace(match_end, verify_arm, 1)
p.write_text(s, encoding='utf-8')

# --- main.rs: CLI parse regression ---
p = Path('src/main.rs')
s = p.read_text(encoding='utf-8')
needle = '''    #[test]
    fn execution_audit_cli_parses() {
'''
start = s.find(needle)
if start < 0:
    raise SystemExit('execution_audit_cli_parses marker not found')
next_test = s.find('\n    #[test]', start + len(needle))
if next_test < 0:
    raise SystemExit('next CLI test marker not found')
if 'fn execution_verify_audit_cli_parses()' not in s:
    test = '''
    #[test]
    fn execution_verify_audit_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "execution",
            "verify-audit",
            "--db",
            "board.db",
            "--participant-id",
            "maker-main",
            "--intent-id",
            "intent-85",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Execution {
                command:
                    execution_audit::ExecutionCommand::VerifyAudit {
                        participant_id,
                        intent_id,
                        ..
                    },
            }) => {
                assert_eq!(participant_id, "maker-main");
                assert_eq!(intent_id, "intent-85");
            }
            other => panic!("unexpected command: {other:?}"),
        }
    }
'''
    s = s[:next_test] + test + s[next_test:]
p.write_text(s, encoding='utf-8')

# --- dedicated integrity tests ---
p = Path('src/execution_integrity_tests.rs')
p.write_text(r'''use rusqlite::{params, Connection};

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
    let report = execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85")
        .unwrap();
    assert!(report.valid);
    assert!(report.violations.is_empty());
    assert!(report.checks.iter().any(|v| v == "valid_committed_audit"));
}

#[test]
fn missing_receipt_is_reported_without_policy_re_evaluation() {
    let conn = fixture();
    let report = execution::verify_execution_audit_integrity(&conn, "maker-main", "missing")
        .unwrap();
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
    let report = execution::verify_execution_audit_integrity(
        &conn,
        "maker-main",
        "intent-broken",
    )
    .unwrap();
    assert!(!report.valid);
    assert!(report.violations.iter().any(|v| v == "message_effect_missing"));
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
    let report = execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85")
        .unwrap();
    assert!(!report.valid);
    assert!(report.violations.iter().any(|v| v == "invalid_receipt_status"));
    assert!(report.violations.iter().any(|v| v == "ingress_metadata_invalid"));
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
    let _ = execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85")
        .unwrap();
    let after: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM execution_receipts WHERE intent_id = ?1",
            params!["intent-85"],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(before, after);
}
''', encoding='utf-8')

p = Path('src/main.rs')
s = p.read_text(encoding='utf-8')
module_marker = '''mod execution_audit;
'''
if 'mod execution_integrity_tests;' not in s:
    if module_marker not in s:
        raise SystemExit('execution_audit module marker not found')
    s = s.replace(module_marker, module_marker + '#[cfg(test)]\nmod execution_integrity_tests;\n', 1)
p.write_text(s, encoding='utf-8')
