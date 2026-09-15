from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


# authorization.rs: canonical read-only policy integrity report.
p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "use rusqlite::{params, Connection, OptionalExtension, Transaction};\n",
    "use std::collections::{HashMap, HashSet};\n\nuse rusqlite::{params, Connection, OptionalExtension, Transaction};\n",
    "authorization imports",
)

anchor = '''#[derive(Debug, Clone)]
struct ParticipantPolicyRow {
'''
insert = r'''#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationIntegrityViolation {
    pub kind: String,
    pub store: String,
    pub grant_id: Option<i64>,
    pub participant_id: Option<String>,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationIntegrityReport {
    pub valid: bool,
    pub durable_grants_scanned: usize,
    pub delegated_grants_scanned: usize,
    pub violations: Vec<AuthorizationIntegrityViolation>,
}

#[derive(Debug, Clone)]
struct ParticipantPolicyRow {
'''
s = replace_once(s, anchor, insert, "integrity report types")

anchor = '''pub fn evaluate_authorization(
'''
functions = r'''pub fn authorization_integrity_schema_current(conn: &Connection) -> rusqlite::Result<bool> {
    Ok(table_has_columns(
        conn,
        "principal_grants",
        &[
            "id",
            "principal_provider",
            "principal_subject",
            "participant_id",
            "capability",
            "resource",
            "status",
        ],
    )? && table_has_columns(
        conn,
        "delegated_grants",
        &[
            "id",
            "principal_provider",
            "principal_subject",
            "participant_id",
            "capability",
            "resource",
            "intent_id",
            "expires_at",
            "one_shot",
            "consumed_at",
            "consumed_intent_id",
            "status",
        ],
    )? && table_has_columns(conn, "web_participants", &["participant_id", "status"])?
        && table_has_columns(
            conn,
            "execution_receipts",
            &["participant_id", "intent_id", "status"],
        )?)
}

pub fn audit_authorization_integrity(
    conn: &Connection,
) -> rusqlite::Result<AuthorizationIntegrityReport> {
    if !authorization_integrity_schema_current(conn)? {
        return Err(rusqlite::Error::InvalidQuery);
    }

    let participants = {
        let mut stmt = conn.prepare("SELECT participant_id FROM web_participants")?;
        stmt.query_map([], |row| row.get::<_, String>(0))?
            .collect::<rusqlite::Result<HashSet<_>>>()?
    };
    let mut violations = Vec::new();
    let mut durable_scopes: HashMap<
        (String, String, String, String, Option<String>),
        Vec<i64>,
    > = HashMap::new();

    let durable_rows = {
        let mut stmt = conn.prepare(
            "SELECT id, principal_provider, principal_subject, participant_id, capability,
                    resource, status
             FROM principal_grants
             ORDER BY id",
        )?;
        stmt.query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, Option<String>>(5)?,
                row.get::<_, String>(6)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };

    for (id, provider, subject, participant_id, capability, resource, status) in &durable_rows {
        audit_required_text(
            &mut violations,
            "principal_grants",
            *id,
            participant_id,
            "malformed_principal_provider",
            provider,
        );
        audit_required_text(
            &mut violations,
            "principal_grants",
            *id,
            participant_id,
            "malformed_principal_subject",
            subject,
        );
        audit_required_text(
            &mut violations,
            "principal_grants",
            *id,
            participant_id,
            "malformed_participant_id",
            participant_id,
        );
        audit_required_text(
            &mut violations,
            "principal_grants",
            *id,
            participant_id,
            "malformed_capability",
            capability,
        );
        if let Some(resource) = resource {
            audit_required_text(
                &mut violations,
                "principal_grants",
                *id,
                participant_id,
                "malformed_resource",
                resource,
            );
        }
        if !participants.contains(participant_id) {
            push_violation(
                &mut violations,
                "missing_participant",
                "principal_grants",
                Some(*id),
                Some(participant_id),
                "grant references no durable participant",
            );
        }
        if !is_known_capability(capability) {
            push_violation(
                &mut violations,
                "unsupported_capability",
                "principal_grants",
                Some(*id),
                Some(participant_id),
                capability,
            );
        }
        if !matches!(status.as_str(), "active" | "inactive") {
            push_violation(
                &mut violations,
                "invalid_status",
                "principal_grants",
                Some(*id),
                Some(participant_id),
                status,
            );
        }
        durable_scopes
            .entry((
                provider.clone(),
                subject.clone(),
                participant_id.clone(),
                capability.clone(),
                resource.clone(),
            ))
            .or_default()
            .push(*id);
    }

    for ((provider, subject, participant_id, capability, resource), ids) in durable_scopes {
        if ids.len() > 1 {
            let id_list = ids
                .iter()
                .map(i64::to_string)
                .collect::<Vec<_>>()
                .join(",");
            push_violation(
                &mut violations,
                "duplicate_durable_scope",
                "principal_grants",
                ids.first().copied(),
                Some(&participant_id),
                &format!(
                    "principal={provider}:{subject};capability={capability};resource={};grant_ids={id_list}",
                    resource.as_deref().unwrap_or("*")
                ),
            );
        }
    }

    let delegated_rows = {
        let mut stmt = conn.prepare(
            "SELECT id, principal_provider, principal_subject, participant_id, capability,
                    resource, intent_id, expires_at, one_shot, consumed_at,
                    consumed_intent_id, status
             FROM delegated_grants
             ORDER BY id",
        )?;
        stmt.query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, Option<String>>(5)?,
                row.get::<_, Option<String>>(6)?,
                row.get::<_, Option<i64>>(7)?,
                row.get::<_, i64>(8)?,
                row.get::<_, Option<i64>>(9)?,
                row.get::<_, Option<String>>(10)?,
                row.get::<_, String>(11)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };

    for (
        id,
        provider,
        subject,
        participant_id,
        capability,
        resource,
        intent_id,
        _expires_at,
        one_shot_raw,
        consumed_at,
        consumed_intent_id,
        status,
    ) in &delegated_rows
    {
        audit_required_text(
            &mut violations,
            "delegated_grants",
            *id,
            participant_id,
            "malformed_principal_provider",
            provider,
        );
        audit_required_text(
            &mut violations,
            "delegated_grants",
            *id,
            participant_id,
            "malformed_principal_subject",
            subject,
        );
        audit_required_text(
            &mut violations,
            "delegated_grants",
            *id,
            participant_id,
            "malformed_participant_id",
            participant_id,
        );
        audit_required_text(
            &mut violations,
            "delegated_grants",
            *id,
            participant_id,
            "malformed_capability",
            capability,
        );
        if let Some(resource) = resource {
            audit_required_text(
                &mut violations,
                "delegated_grants",
                *id,
                participant_id,
                "malformed_resource",
                resource,
            );
        }
        if let Some(intent_id) = intent_id {
            audit_required_text(
                &mut violations,
                "delegated_grants",
                *id,
                participant_id,
                "malformed_intent_id",
                intent_id,
            );
        }
        if !participants.contains(participant_id) {
            push_violation(
                &mut violations,
                "missing_participant",
                "delegated_grants",
                Some(*id),
                Some(participant_id),
                "grant references no durable participant",
            );
        }
        if !is_known_capability(capability) {
            push_violation(
                &mut violations,
                "unsupported_capability",
                "delegated_grants",
                Some(*id),
                Some(participant_id),
                capability,
            );
        }
        if !matches!(status.as_str(), "active" | "inactive") {
            push_violation(
                &mut violations,
                "invalid_status",
                "delegated_grants",
                Some(*id),
                Some(participant_id),
                status,
            );
        }
        if !matches!(*one_shot_raw, 0 | 1) {
            push_violation(
                &mut violations,
                "invalid_one_shot_flag",
                "delegated_grants",
                Some(*id),
                Some(participant_id),
                &one_shot_raw.to_string(),
            );
        }

        let has_consumed_at = consumed_at.is_some();
        let has_consumed_intent = consumed_intent_id.is_some();
        if has_consumed_at != has_consumed_intent {
            push_violation(
                &mut violations,
                "partial_consumption_metadata",
                "delegated_grants",
                Some(*id),
                Some(participant_id),
                "consumed_at and consumed_intent_id must appear together",
            );
        }
        if *one_shot_raw == 0 && (has_consumed_at || has_consumed_intent) {
            push_violation(
                &mut violations,
                "non_one_shot_consumption_metadata",
                "delegated_grants",
                Some(*id),
                Some(participant_id),
                "only one-shot grants may carry consumption metadata",
            );
        }
        if let (Some(bound), Some(consumed)) = (intent_id.as_deref(), consumed_intent_id.as_deref()) {
            if bound != consumed {
                push_violation(
                    &mut violations,
                    "consumed_intent_conflicts_with_binding",
                    "delegated_grants",
                    Some(*id),
                    Some(participant_id),
                    &format!("bound={bound};consumed={consumed}"),
                );
            }
        }
        if *one_shot_raw == 1 && has_consumed_at && has_consumed_intent {
            let consumed_intent = consumed_intent_id.as_deref().expect("checked above");
            if !committed_receipt_exists(conn, participant_id, consumed_intent)? {
                push_violation(
                    &mut violations,
                    "consumed_one_shot_without_committed_receipt",
                    "delegated_grants",
                    Some(*id),
                    Some(participant_id),
                    consumed_intent,
                );
            }
        }
    }

    violations.sort_by(|left, right| {
        left.store
            .cmp(&right.store)
            .then_with(|| left.grant_id.cmp(&right.grant_id))
            .then_with(|| left.kind.cmp(&right.kind))
            .then_with(|| left.detail.cmp(&right.detail))
    });
    Ok(AuthorizationIntegrityReport {
        valid: violations.is_empty(),
        durable_grants_scanned: durable_rows.len(),
        delegated_grants_scanned: delegated_rows.len(),
        violations,
    })
}

fn table_has_columns(
    conn: &Connection,
    table: &str,
    required: &[&str],
) -> rusqlite::Result<bool> {
    let exists: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?1",
        [table],
        |row| row.get(0),
    )?;
    if exists == 0 {
        return Ok(false);
    }
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let columns = stmt
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<rusqlite::Result<HashSet<_>>>()?;
    Ok(required.iter().all(|column| columns.contains(*column)))
}

fn audit_required_text(
    violations: &mut Vec<AuthorizationIntegrityViolation>,
    store: &str,
    grant_id: i64,
    participant_id: &str,
    kind: &str,
    value: &str,
) {
    if value.trim().is_empty() || value.chars().any(char::is_control) {
        push_violation(
            violations,
            kind,
            store,
            Some(grant_id),
            Some(participant_id),
            "value is blank or contains control characters",
        );
    }
}

fn push_violation(
    violations: &mut Vec<AuthorizationIntegrityViolation>,
    kind: &str,
    store: &str,
    grant_id: Option<i64>,
    participant_id: Option<&str>,
    detail: &str,
) {
    violations.push(AuthorizationIntegrityViolation {
        kind: kind.to_owned(),
        store: store.to_owned(),
        grant_id,
        participant_id: participant_id.map(str::to_owned),
        detail: detail.to_owned(),
    });
}

pub fn evaluate_authorization(
'''
s = replace_once(s, anchor, functions, "authorization integrity functions")

# Add canonical report tests before the existing first authorization test.
anchor = '''    #[test]
    fn github_owner_keeps_implicit_post_authority() {
'''
tests = r'''    #[test]
    fn authorization_integrity_accepts_clean_expiry_and_inactive_history() {
        let (_dir, conn) = setup();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource, status)
             VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', 'control-systems', 'inactive')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, expires_at, one_shot)
             VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message',
                     'control-systems', 'expired-history', unixepoch() - 60, 0)",
            [],
        )
        .unwrap();
        identity::set_web_participant_status(&conn, "maker-main", "inactive").unwrap();

        let report = audit_authorization_integrity(&conn).unwrap();
        assert!(report.valid);
        assert_eq!(report.durable_grants_scanned, 1);
        assert_eq!(report.delegated_grants_scanned, 1);
        assert!(report.violations.is_empty());
    }

    #[test]
    fn authorization_integrity_detects_durable_scope_and_reference_corruption() {
        let (_dir, conn) = setup();
        for _ in 0..2 {
            conn.execute(
                "INSERT INTO principal_grants
                    (principal_provider, principal_subject, participant_id, capability, resource)
                 VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', NULL)",
                [],
            )
            .unwrap();
        }
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('oidc:https://issuer.example', 'agent-2', 'maker-main', 'invented_capability', 'x')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('oidc:https://issuer.example', 'agent-3', 'ghost-main', 'post_message', 'x')",
            [],
        )
        .unwrap();

        let report = audit_authorization_integrity(&conn).unwrap();
        assert!(!report.valid);
        let kinds = report
            .violations
            .iter()
            .map(|violation| violation.kind.as_str())
            .collect::<Vec<_>>();
        assert!(kinds.contains(&"duplicate_durable_scope"));
        assert!(kinds.contains(&"unsupported_capability"));
        assert!(kinds.contains(&"missing_participant"));
    }

    #[test]
    fn authorization_integrity_detects_delegated_consumption_corruption() {
        let (_dir, conn) = setup();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, one_shot, consumed_at, consumed_intent_id)
             VALUES ('oidc:https://issuer.example', 'partial', 'maker-main', 'post_message',
                     'control-systems', 'partial-1', 1, unixepoch(), NULL)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, one_shot, consumed_at, consumed_intent_id)
             VALUES ('oidc:https://issuer.example', 'non-one-shot', 'maker-main', 'post_message',
                     'control-systems', NULL, 0, unixepoch(), 'non-one-shot-1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, one_shot, consumed_at, consumed_intent_id)
             VALUES ('oidc:https://issuer.example', 'conflict', 'maker-main', 'post_message',
                     'control-systems', 'bound-1', 1, unixepoch(), 'other-1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, one_shot, consumed_at, consumed_intent_id)
             VALUES ('oidc:https://issuer.example', 'missing-receipt', 'maker-main', 'post_message',
                     'control-systems', 'missing-receipt-1', 1, unixepoch(), 'missing-receipt-1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, one_shot)
             VALUES ('oidc:https://issuer.example', 'unknown-cap', 'maker-main', 'unknown_delegate',
                     'control-systems', 'unknown-cap-1', 0)",
            [],
        )
        .unwrap();

        let report = audit_authorization_integrity(&conn).unwrap();
        assert!(!report.valid);
        let kinds = report
            .violations
            .iter()
            .map(|violation| violation.kind.as_str())
            .collect::<Vec<_>>();
        assert!(kinds.contains(&"partial_consumption_metadata"));
        assert!(kinds.contains(&"non_one_shot_consumption_metadata"));
        assert!(kinds.contains(&"consumed_intent_conflicts_with_binding"));
        assert!(kinds.contains(&"consumed_one_shot_without_committed_receipt"));
        assert!(kinds.contains(&"unsupported_capability"));
    }

    #[test]
    fn github_owner_keeps_implicit_post_authority() {
'''
s = replace_once(s, anchor, tests, "authorization integrity tests")
p.write_text(s, encoding="utf-8")


# grant_admin.rs: add read-only CLI projection and no-mutation legacy-schema test.
p = Path("src/grant_admin.rs")
s = p.read_text(encoding="utf-8")
old = '''    /// Manage durable scoped principal grants separately from delegated grants.
    Durable {
        #[command(subcommand)]
        command: DurableGrantCommand,
    },
}
'''
new = '''    /// Manage durable scoped principal grants separately from delegated grants.
    Durable {
        #[command(subcommand)]
        command: DurableGrantCommand,
    },
    /// Verify authorization-policy object integrity without repairing the database.
    Verify {
        #[arg(long)]
        db: PathBuf,
    },
}
'''
s = replace_once(s, old, new, "grant verify command")

old = '''        GrantCommand::Durable { command } => dispatch_durable(command),
    }
}
'''
new = r'''        GrantCommand::Durable { command } => dispatch_durable(command),
        GrantCommand::Verify { db: path } => {
            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            if !authorization::authorization_integrity_schema_current(&conn)? {
                return Err(format!(
                    "authorization grant schema requires migration: run `conversation-blackboard db init --db {}` before audit",
                    path.display()
                )
                .into());
            }
            let report = authorization::audit_authorization_integrity(&conn)?;
            println!("AUTHORIZATION POLICY INTEGRITY");
            println!("valid                    : {}", report.valid);
            println!(
                "durable_grants_scanned   : {}",
                report.durable_grants_scanned
            );
            println!(
                "delegated_grants_scanned : {}",
                report.delegated_grants_scanned
            );
            println!("violations               : {}", report.violations.len());
            for violation in report.violations {
                println!(
                    "violation                : {}\t{}\t{}\t{}\t{}",
                    violation.kind,
                    violation.store,
                    violation
                        .grant_id
                        .map(|id| id.to_string())
                        .unwrap_or_else(|| "-".to_owned()),
                    violation.participant_id.as_deref().unwrap_or("-"),
                    violation.detail,
                );
            }
            if report.valid {
                Ok(())
            } else {
                Err("authorization policy integrity verification failed".into())
            }
        }
    }
}
'''
s = replace_once(s, old, new, "grant verify dispatch")

anchor = '''    #[test]
    fn durable_create_is_idempotent_and_reactivates_same_scope() {
'''
tests = r'''    #[test]
    fn grant_verify_accepts_clean_database() {
        let (dir, conn) = setup();
        drop(conn);
        dispatch(GrantCommand::Verify {
            db: dir.path().join("board.db"),
        })
        .unwrap();
    }

    #[test]
    fn grant_verify_refuses_legacy_schema_without_mutation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("legacy.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );",
        )
        .unwrap();
        let before: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        drop(conn);

        let error = dispatch(GrantCommand::Verify { db: path.clone() })
            .expect_err("legacy grant schema must be refused")
            .to_string();
        assert!(error.contains("authorization grant schema requires migration"));

        let conn = Connection::open(&path).unwrap();
        let after: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before, after);
        let grant_tables: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'table' AND name IN ('principal_grants', 'delegated_grants')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(grant_tables, 0);
    }

    #[test]
    fn grant_verify_reports_invalid_state_without_repairing_it() {
        let (dir, conn) = setup();
        for _ in 0..2 {
            conn.execute(
                "INSERT INTO principal_grants
                    (principal_provider, principal_subject, participant_id, capability, resource)
                 VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', NULL)",
                [],
            )
            .unwrap();
        }
        let before: i64 = conn
            .query_row("SELECT COUNT(*) FROM principal_grants", [], |row| row.get(0))
            .unwrap();
        drop(conn);

        let error = dispatch(GrantCommand::Verify {
            db: dir.path().join("board.db"),
        })
        .expect_err("duplicate wildcard authority must fail verification")
        .to_string();
        assert!(error.contains("authorization policy integrity verification failed"));

        let conn = db::connect(&dir.path().join("board.db")).unwrap();
        let after: i64 = conn
            .query_row("SELECT COUNT(*) FROM principal_grants", [], |row| row.get(0))
            .unwrap();
        assert_eq!(before, after);
    }

    #[test]
    fn durable_create_is_idempotent_and_reactivates_same_scope() {
'''
s = replace_once(s, anchor, tests, "grant verify tests")
p.write_text(s, encoding="utf-8")


# main.rs: parser regression for top-level grant verify.
p = Path("src/main.rs")
s = p.read_text(encoding="utf-8")
anchor = '''    #[test]
    fn durable_principal_grant_cli_parses() {
'''
test = r'''    #[test]
    fn authorization_grant_verify_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "verify",
            "--db",
            "board.db",
        ])
        .unwrap();
        assert!(matches!(
            cli.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::Verify { .. }
            })
        ));
    }

    #[test]
    fn durable_principal_grant_cli_parses() {
'''
s = replace_once(s, anchor, test, "grant verify parser test")
p.write_text(s, encoding="utf-8")


# operations.md: distinguish storage, execution-evidence, and policy integrity.
p = Path("docs/operations.md")
s = p.read_text(encoding="utf-8")
old = '''Delegated one-shot consumption remains part of the same semantic execution transaction as the committed effect and receipt. Failed semantic execution does not burn one-shot authority.

## GitHub participant ownership
'''
new = r'''Delegated one-shot consumption remains part of the same semantic execution transaction as the committed effect and receipt. Failed semantic execution does not burn one-shot authority.

### Authorization policy integrity

Use the read-only policy audit to verify the grant objects themselves:

```powershell
.\conversation-blackboard.exe grant verify --db <DB>
```

Keep the three integrity questions distinct:

```text
db integrity          SQLite/file structural integrity
execution verify-all  committed semantic execution-evidence integrity
grant verify          authorization-policy object integrity
```

`grant verify` does not migrate, repair, deduplicate, deactivate, or consume authority. It reports malformed/ambiguous grant state such as duplicate durable scopes, missing participant references, unsupported capabilities, and inconsistent delegated one-shot consumption evidence. Normal grant expiry and retained grants for inactive participants are not policy-integrity violations by themselves.

If the authorization schema is too old to audit, migrate explicitly with `db init`; verification never performs that mutation implicitly.

## GitHub participant ownership
'''
s = replace_once(s, old, new, "operations integrity documentation")
p.write_text(s, encoding="utf-8")
