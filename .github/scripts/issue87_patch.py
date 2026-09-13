from pathlib import Path

# --- execution.rs ---
p = Path('src/execution.rs')
s = p.read_text(encoding='utf-8')

old_struct = '''#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IngressAuditRecord {
    pub delivery_id: String,
    pub intent_id: String,
    pub transport: String,
    pub external_ref: String,
    pub principal: Principal,
}
'''
new_struct = '''#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IngressAuditRecord {
    pub participant_id: String,
    pub delivery_id: String,
    pub intent_id: String,
    pub transport: String,
    pub external_ref: String,
    pub principal: Principal,
}
'''
if old_struct in s:
    s = s.replace(old_struct, new_struct, 1)

start = s.index('pub fn ensure_execution_tables(conn: &Connection) -> rusqlite::Result<()> {')
end = s.index('\npub fn get_execution_receipt(', start)
new_ensure = r'''pub fn ensure_execution_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS ingress_provenance (
            delivery_id         TEXT PRIMARY KEY,
            participant_id      TEXT,
            intent_id           TEXT NOT NULL,
            transport           TEXT NOT NULL,
            external_ref        TEXT NOT NULL,
            principal_provider  TEXT NOT NULL,
            principal_subject   TEXT NOT NULL,
            created_at          INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_ingress_provenance_intent
            ON ingress_provenance(intent_id);

        CREATE TABLE IF NOT EXISTS execution_receipts (
            participant_id  TEXT NOT NULL,
            intent_id       TEXT NOT NULL,
            intent_hash     TEXT NOT NULL,
            capability      TEXT NOT NULL,
            message_id      INTEGER NOT NULL,
            status          TEXT NOT NULL,
            created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (participant_id, intent_id)
        );
        CREATE INDEX IF NOT EXISTS idx_execution_receipts_message
            ON execution_receipts(message_id);

        CREATE TABLE IF NOT EXISTS execution_authorization_provenance (
            participant_id  TEXT NOT NULL,
            intent_id       TEXT NOT NULL,
            source          TEXT NOT NULL,
            reason          TEXT NOT NULL,
            grant_id        INTEGER,
            created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (participant_id, intent_id)
        );",
    )?;

    let has_participant_id = {
        let mut stmt = conn.prepare("PRAGMA table_info(ingress_provenance)")?;
        let columns = stmt
            .query_map([], |row| row.get::<_, String>(1))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        columns.iter().any(|column| column == "participant_id")
    };
    if !has_participant_id {
        conn.execute(
            "ALTER TABLE ingress_provenance ADD COLUMN participant_id TEXT",
            [],
        )?;
    }

    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_ingress_provenance_execution
            ON ingress_provenance(participant_id, intent_id);",
    )?;

    conn.execute(
        "UPDATE ingress_provenance
         SET participant_id = (
             SELECT MIN(er.participant_id)
             FROM execution_receipts er
             WHERE er.intent_id = ingress_provenance.intent_id
         )
         WHERE participant_id IS NULL
           AND 1 = (
             SELECT COUNT(DISTINCT er.participant_id)
             FROM execution_receipts er
             WHERE er.intent_id = ingress_provenance.intent_id
           )",
        [],
    )?;

    Ok(())
}
'''
s = s[:start] + new_ensure + s[end:]

start = s.index('pub fn get_execution_audit_bundle(')
end = s.index('\npub fn verify_execution_audit_integrity(', start)
new_bundle = r'''pub fn get_execution_audit_bundle(
    conn: &Connection,
    participant_id: &str,
    intent_id: &str,
) -> rusqlite::Result<Option<ExecutionAuditBundle>> {
    let Some(receipt) = get_execution_receipt(conn, participant_id, intent_id)? else {
        return Ok(None);
    };
    let authorization = get_authorization_provenance(conn, participant_id, intent_id)?;
    let mut stmt = conn.prepare(
        "SELECT participant_id, delivery_id, intent_id, transport, external_ref,
                principal_provider, principal_subject
         FROM ingress_provenance
         WHERE participant_id = ?1 AND intent_id = ?2
         ORDER BY created_at, delivery_id",
    )?;
    let ingress = stmt
        .query_map(params![participant_id, intent_id], |row| {
            Ok(IngressAuditRecord {
                participant_id: row.get(0)?,
                delivery_id: row.get(1)?,
                intent_id: row.get(2)?,
                transport: row.get(3)?,
                external_ref: row.get(4)?,
                principal: Principal {
                    provider: row.get(5)?,
                    subject: row.get(6)?,
                },
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(Some(ExecutionAuditBundle {
        receipt,
        authorization,
        ingress,
    }))
}
'''
s = s[:start] + new_bundle + s[end:]

start = s.index('pub fn verify_execution_audit_integrity(')
end = s.index('\npub fn execute_message_intent(', start)
old_verify = s[start:end]
needle_start = old_verify.index('    let mut stmt = conn.prepare(')
needle_end = old_verify.index('\n    let valid = violations.is_empty();', needle_start)
new_ingress_verify = r'''    let mut stmt = conn.prepare(
        "SELECT participant_id, delivery_id, intent_id, transport, external_ref,
                principal_provider, principal_subject
         FROM ingress_provenance
         WHERE intent_id = ?1
         ORDER BY created_at, delivery_id",
    )?;
    let ingress = stmt
        .query_map([intent_id], |row| {
            Ok((
                row.get::<_, Option<String>>(0)?,
                IngressAuditRecord {
                    participant_id: row.get::<_, Option<String>>(0)?.unwrap_or_default(),
                    delivery_id: row.get(1)?,
                    intent_id: row.get(2)?,
                    transport: row.get(3)?,
                    external_ref: row.get(4)?,
                    principal: Principal {
                        provider: row.get(5)?,
                        subject: row.get(6)?,
                    },
                },
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;

    let mut ingress_metadata_invalid = false;
    let mut ingress_participant_unbound = false;
    let mut target_ingress_count = 0usize;
    for (binding, row) in &ingress {
        match binding.as_deref() {
            None | Some("") => ingress_participant_unbound = true,
            Some(bound_participant) if bound_participant == participant_id => {
                target_ingress_count += 1;
                if row.intent_id != intent_id || row.participant_id != participant_id {
                    violations.push("ingress_binding_mismatch".to_owned());
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
            Some(_) => {}
        }
    }

    if ingress_participant_unbound {
        violations.push("ingress_participant_unbound".to_owned());
    }
    if target_ingress_count == 0 {
        violations.push("ingress_missing".to_owned());
    } else if !violations.iter().any(|v| v == "ingress_binding_mismatch") {
        checks.push("ingress_binding_valid".to_owned());
    }
    if ingress_metadata_invalid {
        violations.push("ingress_metadata_invalid".to_owned());
    } else if target_ingress_count > 0 {
        checks.push("ingress_metadata_valid".to_owned());
    }
'''
old_verify = old_verify[:needle_start] + new_ingress_verify + old_verify[needle_end:]
s = s[:start] + old_verify + s[end:]

# record_execution_in_tx participant binding
old = '''    let existing_delivery: Option<(String, String, String, String, String)> = tx
        .query_row(
            "SELECT intent_id, transport, external_ref, principal_provider, principal_subject
             FROM ingress_provenance WHERE delivery_id = ?1",
            [&ingress.delivery_id],
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

    if let Some(existing) = existing_delivery {
        let expected = (
            ingress.intent_id.clone(),
            ingress.transport.clone(),
            ingress.external_ref.clone(),
            ingress.principal.provider.clone(),
            ingress.principal.subject.clone(),
        );
'''
new = '''    let existing_delivery: Option<(Option<String>, String, String, String, String, String)> = tx
        .query_row(
            "SELECT participant_id, intent_id, transport, external_ref,
                    principal_provider, principal_subject
             FROM ingress_provenance WHERE delivery_id = ?1",
            [&ingress.delivery_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                ))
            },
        )
        .optional()?;

    if let Some(existing) = existing_delivery {
        let expected = (
            Some(receipt.participant_id.clone()),
            ingress.intent_id.clone(),
            ingress.transport.clone(),
            ingress.external_ref.clone(),
            ingress.principal.provider.clone(),
            ingress.principal.subject.clone(),
        );
'''
if old not in s:
    raise SystemExit('existing delivery block not found')
s = s.replace(old, new, 1)

old = '''        tx.execute(
            "INSERT INTO ingress_provenance
                (delivery_id, intent_id, transport, external_ref, principal_provider, principal_subject)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                ingress.delivery_id,
                ingress.intent_id,
                ingress.transport,
                ingress.external_ref,
                ingress.principal.provider,
                ingress.principal.subject
            ],
        )?;
'''
new = '''        tx.execute(
            "INSERT INTO ingress_provenance
                (delivery_id, participant_id, intent_id, transport, external_ref,
                 principal_provider, principal_subject)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                ingress.delivery_id,
                receipt.participant_id,
                ingress.intent_id,
                ingress.transport,
                ingress.external_ref,
                ingress.principal.provider,
                ingress.principal.subject
            ],
        )?;
'''
if old not in s:
    raise SystemExit('ingress insert block not found')
s = s.replace(old, new, 1)

p.write_text(s, encoding='utf-8')

# --- contract_schema.rs ---
p = Path('src/contract_schema.rs')
s = p.read_text(encoding='utf-8')
old = '''        "properties": {
            "delivery_id": delivery_id_schema(),
            "intent_id": intent_id_schema(),
            "transport": {"type": "string"},
            "external_ref": {"type": "string"},
            "principal": principal_schema()
        },
        "required": ["delivery_id", "intent_id", "transport", "external_ref", "principal"],
'''
new = '''        "properties": {
            "participant_id": participant_id_schema(),
            "delivery_id": delivery_id_schema(),
            "intent_id": intent_id_schema(),
            "transport": {"type": "string"},
            "external_ref": {"type": "string"},
            "principal": principal_schema()
        },
        "required": ["participant_id", "delivery_id", "intent_id", "transport", "external_ref", "principal"],
'''
if old not in s:
    raise SystemExit('ingress schema block not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

# --- execution_audit.rs ---
p = Path('src/execution_audit.rs')
s = p.read_text(encoding='utf-8')
old = '''                    "delivery        : {}\\t{}\\t{}\\t{}:{}\\t{}",
                    delivery.delivery_id,
                    delivery.transport,
                    delivery.external_ref,
                    delivery.principal.provider,
                    delivery.principal.subject,
                    delivery.intent_id,
'''
new = '''                    "delivery        : {}\\t{}\\t{}\\t{}\\t{}:{}\\t{}",
                    delivery.participant_id,
                    delivery.delivery_id,
                    delivery.transport,
                    delivery.external_ref,
                    delivery.principal.provider,
                    delivery.principal.subject,
                    delivery.intent_id,
'''
if old not in s:
    raise SystemExit('execution audit delivery print block not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

# --- OpenAPI ingress shape ---
p = Path('integrations/openapi.yaml')
s = p.read_text(encoding='utf-8')
old = '''    IngressAuditRecord:
      type: object
      additionalProperties: false
      required: [delivery_id, intent_id, transport, external_ref, principal]
      properties:
        delivery_id:
          $ref: '#/components/schemas/DeliveryId'
'''
new = '''    IngressAuditRecord:
      type: object
      additionalProperties: false
      required: [participant_id, delivery_id, intent_id, transport, external_ref, principal]
      properties:
        participant_id:
          $ref: '#/components/schemas/ParticipantId'
        delivery_id:
          $ref: '#/components/schemas/DeliveryId'
'''
if old not in s:
    raise SystemExit('OpenAPI IngressAuditRecord block not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

# --- focused regression tests ---
p = Path('src/execution_integrity_tests.rs')
s = p.read_text(encoding='utf-8')
if 'same_intent_across_participants_keeps_ingress_separate' not in s:
    s += r'''

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
        conn.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES (?1, 'shared-intent', 'hash', 'post_message', ?2, 'committed')",
            params![participant, message_id],
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

    assert!(execution::verify_execution_audit_integrity(&conn, "alpha-main", "shared-intent")
        .unwrap()
        .valid);
    assert!(execution::verify_execution_audit_integrity(&conn, "beta-main", "shared-intent")
        .unwrap()
        .valid);
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
        conn.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES (?1, 'ambiguous-intent', 'hash', 'post_message', ?2, 'committed')",
            params![participant, message_id],
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
'''
p.write_text(s, encoding='utf-8')
