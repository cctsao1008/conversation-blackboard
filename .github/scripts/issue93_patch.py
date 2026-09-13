from pathlib import Path

# --- src/execution.rs -------------------------------------------------------
p = Path('src/execution.rs')
s = p.read_text(encoding='utf-8')

s = s.replace(
'''pub struct AuthorizationProvenance {
    pub participant_id: String,
    pub intent_id: String,
    pub source: String,
    pub reason: String,
    pub grant_id: Option<i64>,
}''',
'''pub struct AuthorizationProvenance {
    pub participant_id: String,
    pub intent_id: String,
    pub principal: Option<Principal>,
    pub capability: Option<String>,
    pub resource: Option<String>,
    pub source: String,
    pub reason: String,
    pub grant_id: Option<i64>,
}''', 1)

s = s.replace(
'''        CREATE TABLE IF NOT EXISTS execution_authorization_provenance (
            participant_id  TEXT NOT NULL,
            intent_id       TEXT NOT NULL,
            source          TEXT NOT NULL,
            reason          TEXT NOT NULL,
            grant_id        INTEGER,
            created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (participant_id, intent_id)
        );",''',
'''        CREATE TABLE IF NOT EXISTS execution_authorization_provenance (
            participant_id      TEXT NOT NULL,
            intent_id           TEXT NOT NULL,
            principal_provider  TEXT,
            principal_subject   TEXT,
            capability          TEXT,
            resource            TEXT,
            source              TEXT NOT NULL,
            reason              TEXT NOT NULL,
            grant_id            INTEGER,
            created_at          INTEGER NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (participant_id, intent_id)
        );",''', 1)

needle = '''    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_ingress_provenance_execution
            ON ingress_provenance(participant_id, intent_id);",
    )?;
'''
addition = needle + '''
    let authorization_columns = {
        let mut stmt = conn.prepare("PRAGMA table_info(execution_authorization_provenance)")?;
        stmt.query_map([], |row| row.get::<_, String>(1))?
            .collect::<rusqlite::Result<Vec<_>>>()?
    };
    for (column, sql_type) in [
        ("principal_provider", "TEXT"),
        ("principal_subject", "TEXT"),
        ("capability", "TEXT"),
        ("resource", "TEXT"),
    ] {
        if !authorization_columns.iter().any(|existing| existing == column) {
            conn.execute(
                &format!(
                    "ALTER TABLE execution_authorization_provenance ADD COLUMN {column} {sql_type}"
                ),
                [],
            )?;
        }
    }

    // Capability is safely recoverable from the committed receipt. Principal and
    // resource are intentionally not reconstructed from current authorization policy.
    conn.execute(
        "UPDATE execution_authorization_provenance
         SET capability = (
             SELECT er.capability
             FROM execution_receipts er
             WHERE er.participant_id = execution_authorization_provenance.participant_id
               AND er.intent_id = execution_authorization_provenance.intent_id
         )
         WHERE capability IS NULL
           AND EXISTS (
             SELECT 1 FROM execution_receipts er
             WHERE er.participant_id = execution_authorization_provenance.participant_id
               AND er.intent_id = execution_authorization_provenance.intent_id
           )",
        [],
    )?;
'''
if needle not in s:
    raise SystemExit('execution index insertion point not found')
s = s.replace(needle, addition, 1)

old = '''        "SELECT participant_id, intent_id, source, reason, grant_id
         FROM execution_authorization_provenance
         WHERE participant_id = ?1 AND intent_id = ?2",
        params![participant_id, intent_id],
        |row| {
            Ok(AuthorizationProvenance {
                participant_id: row.get(0)?,
                intent_id: row.get(1)?,
                source: row.get(2)?,
                reason: row.get(3)?,
                grant_id: row.get(4)?,
            })
        },'''
new = '''        "SELECT participant_id, intent_id, principal_provider, principal_subject,
                capability, resource, source, reason, grant_id
         FROM execution_authorization_provenance
         WHERE participant_id = ?1 AND intent_id = ?2",
        params![participant_id, intent_id],
        |row| {
            let provider = row.get::<_, Option<String>>(2)?;
            let subject = row.get::<_, Option<String>>(3)?;
            let principal = match (provider, subject) {
                (Some(provider), Some(subject)) => Some(Principal { provider, subject }),
                _ => None,
            };
            Ok(AuthorizationProvenance {
                participant_id: row.get(0)?,
                intent_id: row.get(1)?,
                principal,
                capability: row.get(4)?,
                resource: row.get(5)?,
                source: row.get(6)?,
                reason: row.get(7)?,
                grant_id: row.get(8)?,
            })
        },'''
if old not in s:
    raise SystemExit('authorization read block not found')
s = s.replace(old, new, 1)

# Preserve expected message resource for authorization scope verification.
s = s.replace(
'''    let message = conn
        .query_row(''',
'''    let mut expected_authorization_resource: Option<String> = None;
    let message = conn
        .query_row(''', 1)
s = s.replace(
'''    if let Some((channel, instance, conversation_ref, kind, body, reply_to)) = message {
        checks.push("message_effect_present".to_owned());''',
'''    if let Some((channel, instance, conversation_ref, kind, body, reply_to)) = message {
        expected_authorization_resource = Some(channel.clone());
        checks.push("message_effect_present".to_owned());''', 1)

old = '''            if let Some(auth) = get_authorization_provenance(conn, participant_id, intent_id)? {
                if auth.participant_id != participant_id || auth.intent_id != intent_id {
                    violations.push("authorization_binding_mismatch".to_owned());
                }
            }'''
new = '''            if let Some(auth) = get_authorization_provenance(conn, participant_id, intent_id)? {
                if auth.participant_id != participant_id || auth.intent_id != intent_id {
                    violations.push("authorization_binding_mismatch".to_owned());
                }
                match auth.principal.as_ref() {
                    Some(principal)
                        if !principal.provider.trim().is_empty()
                            && !principal.subject.trim().is_empty() =>
                    {
                        checks.push("authorization_principal_bound".to_owned());
                    }
                    Some(_) => violations.push("authorization_metadata_invalid".to_owned()),
                    None => violations.push("authorization_principal_unbound".to_owned()),
                }
                match auth.capability.as_deref() {
                    Some(capability) if capability == receipt.capability => {
                        checks.push("authorization_capability_matches_receipt".to_owned());
                    }
                    Some(_) => violations.push("authorization_capability_mismatch".to_owned()),
                    None => violations.push("authorization_scope_unbound".to_owned()),
                }
                match auth.resource.as_deref() {
                    Some(resource) if resource.trim().is_empty() => {
                        violations.push("authorization_metadata_invalid".to_owned());
                    }
                    Some(resource)
                        if expected_authorization_resource.as_deref() == Some(resource) =>
                    {
                        checks.push("authorization_resource_matches_effect".to_owned());
                    }
                    Some(_) => violations.push("authorization_resource_mismatch".to_owned()),
                    None => violations.push("authorization_scope_unbound".to_owned()),
                }
            }'''
if old not in s:
    raise SystemExit('authorization integrity block not found')
s = s.replace(old, new, 1)

s = s.replace(
'''        record_authorization_provenance_in_tx(&tx, &receipt, &authorization_decision)?;''',
'''        record_authorization_provenance_in_tx(
            &tx,
            &receipt,
            &request.authority.principal,
            &request.intent.resource,
            &authorization_decision,
        )?;''', 1)

old = '''fn record_authorization_provenance_in_tx(
    tx: &Transaction<'_>,
    receipt: &ExecutionReceipt,
    decision: &authorization::AuthorizationDecision,
) -> rusqlite::Result<()> {'''
new = '''fn record_authorization_provenance_in_tx(
    tx: &Transaction<'_>,
    receipt: &ExecutionReceipt,
    principal: &Principal,
    resource: &str,
    decision: &authorization::AuthorizationDecision,
) -> rusqlite::Result<()> {'''
if old not in s:
    raise SystemExit('record auth function signature not found')
s = s.replace(old, new, 1)

old = '''        "INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, source, reason, grant_id)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        params![
            receipt.participant_id,
            receipt.intent_id,
            decision.source,
            decision.reason,
            decision.grant_id
        ],'''
new = '''        "INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, principal_provider, principal_subject,
             capability, resource, source, reason, grant_id)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            receipt.participant_id,
            receipt.intent_id,
            principal.provider,
            principal.subject,
            receipt.capability,
            resource,
            decision.source,
            decision.reason,
            decision.grant_id
        ],'''
if old not in s:
    raise SystemExit('record auth insert block not found')
s = s.replace(old, new, 1)

# Extend existing fresh-execution assertions rather than creating parallel setup.
needle = '''        assert_eq!(provenance.source, "implicit_authority");
        assert_eq!(provenance.reason, "implicit_participant_hmac");'''
replacement = needle + '''
        assert_eq!(
            provenance.principal,
            Some(Principal {
                provider: "participant-hmac".into(),
                subject: "maker-main".into(),
            })
        );
        assert_eq!(provenance.capability.as_deref(), Some("post_message"));
        assert_eq!(provenance.resource.as_deref(), Some("blackboard-lounge"));'''
if needle not in s:
    raise SystemExit('fresh auth assertions insertion point not found')
s = s.replace(needle, replacement, 1)

needle = '''        assert_eq!(provenance.source, "delegated_grants");
        assert_eq!(provenance.reason, "delegated_grant_match");
        assert!(provenance.grant_id.is_some());'''
replacement = needle + '''
        assert_eq!(provenance.principal, Some(authority.principal.clone()));
        assert_eq!(provenance.capability.as_deref(), Some("post_message"));
        assert_eq!(provenance.resource.as_deref(), Some("blackboard-lounge"));'''
if needle not in s:
    raise SystemExit('delegated auth assertions insertion point not found')
s = s.replace(needle, replacement, 1)

p.write_text(s, encoding='utf-8')

# --- src/contract_schema.rs -------------------------------------------------
p = Path('src/contract_schema.rs')
s = p.read_text(encoding='utf-8')
old = '''            "participant_id": participant_id_schema(),
            "intent_id": intent_id_schema(),
            "source": {"type": "string"},
            "reason": {"type": "string"},
            "grant_id": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
        },
        "required": ["participant_id", "intent_id", "source", "reason", "grant_id"],'''
new = '''            "participant_id": participant_id_schema(),
            "intent_id": intent_id_schema(),
            "principal": {"anyOf": [principal_schema(), {"type": "null"}]},
            "capability": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "resource": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "source": {"type": "string"},
            "reason": {"type": "string"},
            "grant_id": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
        },
        "required": ["participant_id", "intent_id", "principal", "capability", "resource", "source", "reason", "grant_id"],'''
if old not in s:
    raise SystemExit('authorization schema block not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

# --- src/execution_integrity_tests.rs --------------------------------------
p = Path('src/execution_integrity_tests.rs')
s = p.read_text(encoding='utf-8')
# Make the canonical valid fixture a fresh-context-equivalent row.
s = s.replace(
'''INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, source, reason, grant_id)
         VALUES ('maker-main', 'intent-85', 'implicit_authority', 'implicit_participant_hmac', NULL)''',
'''INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, principal_provider, principal_subject,
             capability, resource, source, reason, grant_id)
         VALUES ('maker-main', 'intent-85', 'participant-hmac', 'maker-main',
                 'post_message', 'blackboard-lounge',
                 'implicit_authority', 'implicit_participant_hmac', NULL)''')

addition = r'''

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
    let report = execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85")
        .unwrap();
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
    let report = execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85")
        .unwrap();
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

    execution::ensure_execution_tables(&conn).unwrap();
    let provenance = execution::get_authorization_provenance(&conn, "maker-main", "intent-85")
        .unwrap()
        .unwrap();
    assert_eq!(provenance.capability.as_deref(), Some("post_message"));
    assert!(provenance.principal.is_none());
    assert!(provenance.resource.is_none());

    let report = execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85")
        .unwrap();
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
'''
if 'fn legacy_authorization_backfills_only_deterministic_capability()' not in s:
    s += addition
p.write_text(s, encoding='utf-8')
