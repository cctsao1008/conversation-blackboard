from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing marker: {label}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Canonical database-wide sweep read model.
# ---------------------------------------------------------------------------
p = Path("src/execution.rs")
s = p.read_text(encoding="utf-8")

struct_marker = '''#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionAuditIntegrityReport {
    pub participant_id: String,
    pub intent_id: String,
    pub valid: bool,
    pub checks: Vec<String>,
    pub violations: Vec<String>,
}

#[derive(Debug)]
'''

struct_replacement = '''#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionAuditIntegrityReport {
    pub participant_id: String,
    pub intent_id: String,
    pub valid: bool,
    pub checks: Vec<String>,
    pub violations: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionAuditOrphanEvidence {
    pub kind: String,
    pub participant_id: Option<String>,
    pub intent_id: String,
    pub reference: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionAuditSweepReport {
    pub valid: bool,
    pub executions_scanned: usize,
    pub invalid_executions: Vec<ExecutionAuditIntegrityReport>,
    pub orphan_evidence: Vec<ExecutionAuditOrphanEvidence>,
}

#[derive(Debug)]
'''
s = replace_once(s, struct_marker, struct_replacement, "sweep structs")

execute_marker = '''pub fn execute_message_intent(
'''

sweep_fn = r'''pub fn sweep_execution_audit_integrity(
    conn: &Connection,
) -> rusqlite::Result<ExecutionAuditSweepReport> {
    let identities = {
        let mut stmt = conn.prepare(
            "SELECT participant_id, intent_id
             FROM execution_receipts
             ORDER BY participant_id, intent_id",
        )?;
        stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };

    let executions_scanned = identities.len();
    let mut invalid_executions = Vec::new();
    for (participant_id, intent_id) in identities {
        let report = verify_execution_audit_integrity(conn, &participant_id, &intent_id)?;
        if !report.valid {
            invalid_executions.push(report);
        }
    }

    let mut orphan_evidence = Vec::new();

    {
        let mut stmt = conn.prepare(
            "SELECT a.participant_id, a.intent_id, a.grant_id
             FROM execution_authorization_provenance AS a
             WHERE NOT EXISTS (
                 SELECT 1
                 FROM execution_receipts AS r
                 WHERE r.participant_id = a.participant_id
                   AND r.intent_id = a.intent_id
             )
             ORDER BY a.participant_id, a.intent_id",
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<i64>>(2)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for (participant_id, intent_id, grant_id) in rows {
            orphan_evidence.push(ExecutionAuditOrphanEvidence {
                kind: "orphan_authorization_provenance".to_owned(),
                participant_id: Some(participant_id),
                intent_id,
                reference: grant_id.map(|id| format!("grant_id={id}")),
            });
        }
    }

    {
        let mut stmt = conn.prepare(
            "SELECT i.participant_id, i.intent_id, i.delivery_id, r.participant_id
             FROM ingress_provenance AS i
             LEFT JOIN execution_receipts AS r
               ON r.participant_id = i.participant_id
              AND r.intent_id = i.intent_id
             ORDER BY i.intent_id, i.delivery_id",
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok((
                    row.get::<_, Option<String>>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for (participant_id, intent_id, delivery_id, matched_participant) in rows {
            match participant_id {
                None => orphan_evidence.push(ExecutionAuditOrphanEvidence {
                    kind: "unbound_legacy_ingress".to_owned(),
                    participant_id: None,
                    intent_id,
                    reference: Some(delivery_id),
                }),
                Some(participant_id) if participant_id.trim().is_empty() => {
                    orphan_evidence.push(ExecutionAuditOrphanEvidence {
                        kind: "unbound_legacy_ingress".to_owned(),
                        participant_id: None,
                        intent_id,
                        reference: Some(delivery_id),
                    });
                }
                Some(participant_id) if matched_participant.is_none() => {
                    orphan_evidence.push(ExecutionAuditOrphanEvidence {
                        kind: "orphan_ingress_provenance".to_owned(),
                        participant_id: Some(participant_id),
                        intent_id,
                        reference: Some(delivery_id),
                    });
                }
                Some(_) => {}
            }
        }
    }

    {
        let mut stmt = conn.prepare(
            "SELECT n.instance, n.nonce, n.message_id
             FROM navigation_writes AS n
             WHERE NOT EXISTS (
                 SELECT 1
                 FROM execution_receipts AS r
                 WHERE r.participant_id = n.instance
                   AND r.intent_id = n.nonce
             )
             ORDER BY n.instance, n.nonce",
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for (participant_id, intent_id, message_id) in rows {
            orphan_evidence.push(ExecutionAuditOrphanEvidence {
                kind: "orphan_navigation_reservation".to_owned(),
                participant_id: Some(participant_id),
                intent_id,
                reference: Some(format!("message_id={message_id}")),
            });
        }
    }

    orphan_evidence.sort_by(|left, right| {
        left.kind
            .cmp(&right.kind)
            .then_with(|| left.participant_id.cmp(&right.participant_id))
            .then_with(|| left.intent_id.cmp(&right.intent_id))
            .then_with(|| left.reference.cmp(&right.reference))
    });

    let valid = invalid_executions.is_empty() && orphan_evidence.is_empty();
    Ok(ExecutionAuditSweepReport {
        valid,
        executions_scanned,
        invalid_executions,
        orphan_evidence,
    })
}

'''
s = replace_once(s, execute_marker, sweep_fn + execute_marker, "sweep function insertion")
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# Operator CLI: execution verify-all --db <path>
# ---------------------------------------------------------------------------
p = Path("src/execution_audit.rs")
s = p.read_text(encoding="utf-8")

enum_marker = '''    VerifyAudit {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        intent_id: String,
    },
}
'''

enum_replacement = '''    VerifyAudit {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        intent_id: String,
    },
    /// Verify the entire committed execution corpus and discover orphan evidence.
    #[command(name = "verify-all")]
    VerifyAll {
        #[arg(long)]
        db: PathBuf,
    },
}
'''
s = replace_once(s, enum_marker, enum_replacement, "verify-all enum variant")

match_marker = '''            if report.valid {
                Ok(())
            } else {
                Err("execution audit integrity verification failed".into())
            }
        }
    }
}
'''

match_replacement = '''            if report.valid {
                Ok(())
            } else {
                Err("execution audit integrity verification failed".into())
            }
        }
        ExecutionCommand::VerifyAll { db: path } => {
            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            require_current_execution_schema(&conn, &path)?;
            let report = execution::sweep_execution_audit_integrity(&conn)?;

            println!("EXECUTION AUDIT SWEEP");
            println!("valid              : {}", report.valid);
            println!("executions_scanned : {}", report.executions_scanned);
            println!("invalid_executions : {}", report.invalid_executions.len());
            println!("orphan_evidence    : {}", report.orphan_evidence.len());
            for invalid in &report.invalid_executions {
                println!(
                    "invalid_execution  : {}\\t{}\\t{}",
                    invalid.participant_id,
                    invalid.intent_id,
                    invalid.violations.join(",")
                );
            }
            for orphan in &report.orphan_evidence {
                println!(
                    "orphan_evidence     : {}\\t{}\\t{}\\t{}",
                    orphan.kind,
                    orphan.participant_id.as_deref().unwrap_or("-"),
                    orphan.intent_id,
                    orphan.reference.as_deref().unwrap_or("-")
                );
            }

            if report.valid {
                Ok(())
            } else {
                Err("execution audit sweep verification failed".into())
            }
        }
    }
}
'''
s = replace_once(s, match_marker, match_replacement, "verify-all dispatch")
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# Integrity sweep regression coverage.
# ---------------------------------------------------------------------------
p = Path("src/execution_integrity_tests.rs")
s = p.read_text(encoding="utf-8")

shared_marker = '''    assert!(
        execution::verify_execution_audit_integrity(&conn, "beta-main", "shared-intent")
            .unwrap()
            .valid
    );
}
'''
shared_replacement = '''    assert!(
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
'''
s = replace_once(s, shared_marker, shared_replacement, "same-intent sweep isolation")

extra_tests = r'''

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
'''
s += extra_tests
p.write_text(s, encoding="utf-8")
