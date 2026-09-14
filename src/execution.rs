use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::{
    authorization, identity,
    model::{Identity, Message},
};

#[cfg(test)]
use crate::db;

pub const MAX_INTENT_ID_BYTES: usize = 256;
pub const POST_MESSAGE_CAPABILITY: &str = "post_message";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Principal {
    pub provider: String,
    pub subject: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AuthorityContext {
    pub principal: Principal,
    pub mechanism: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IntentEnvelope {
    pub intent_id: String,
    pub participant_id: String,
    pub conversation_ref: Option<String>,
    pub capability: String,
    pub resource: String,
    pub request_hash: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IngressProvenance {
    pub delivery_id: String,
    pub intent_id: String,
    pub transport: String,
    pub external_ref: String,
    pub principal: Principal,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionReceipt {
    pub participant_id: String,
    pub intent_id: String,
    pub intent_hash: String,
    pub capability: String,
    pub message_id: i64,
    pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AuthorizationProvenance {
    pub participant_id: String,
    pub intent_id: String,
    pub principal: Option<Principal>,
    pub capability: Option<String>,
    pub resource: Option<String>,
    pub source: String,
    pub reason: String,
    pub grant_id: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IngressAuditRecord {
    pub participant_id: String,
    pub delivery_id: String,
    pub intent_id: String,
    pub transport: String,
    pub external_ref: String,
    pub principal: Principal,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionAuditBundle {
    pub receipt: ExecutionReceipt,
    pub authorization: Option<AuthorizationProvenance>,
    pub ingress: Vec<IngressAuditRecord>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
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
pub struct MessageExecutionRequest<'a> {
    pub authority: &'a AuthorityContext,
    pub ingress: &'a IngressProvenance,
    pub intent: &'a IntentEnvelope,
    pub channel: &'a str,
    pub kind: &'a str,
    pub body: &'a str,
    pub reply_to: Option<i64>,
}

#[derive(Debug)]
pub enum MessageExecutionResult {
    Created(Message),
    Existing(Message),
    IntentConflict,
    ReplyTargetNotFound,
    ChannelArchived,
    AuthorizationDenied,
}

pub fn github_delivery_id(repository_id: u64, issue_number: i64) -> String {
    format!("github:{repository_id}:issue:{issue_number}")
}

pub fn github_intent_id(
    explicit: Option<&str>,
    repository_id: u64,
    issue_number: i64,
) -> Result<String, ()> {
    match explicit {
        Some(value) => normalize_intent_id(value),
        None => Ok(github_delivery_id(repository_id, issue_number)),
    }
}

pub fn normalize_intent_id(value: &str) -> Result<String, ()> {
    let value = value.trim();
    if value.is_empty() || value.len() > MAX_INTENT_ID_BYTES || value.chars().any(char::is_control)
    {
        return Err(());
    }
    Ok(value.to_owned())
}

pub fn message_request_hash(
    channel: &str,
    kind: &str,
    body: &str,
    conversation_ref: Option<&str>,
    reply_to: Option<i64>,
) -> String {
    identity::hash_token(
        &json!({
            "channel": channel,
            "kind": kind,
            "body": body,
            "conversation_ref": conversation_ref,
            "reply_to": reply_to,
        })
        .to_string(),
    )
}

pub fn migrate_execution_schema(conn: &Connection) -> rusqlite::Result<()> {
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

    let authorization_columns = {
        let mut stmt = conn.prepare("PRAGMA table_info(execution_authorization_provenance)")?;
        let columns = stmt
            .query_map([], |row| row.get::<_, String>(1))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        columns
    };
    for (column, sql_type) in [
        ("principal_provider", "TEXT"),
        ("principal_subject", "TEXT"),
        ("capability", "TEXT"),
        ("resource", "TEXT"),
    ] {
        if !authorization_columns
            .iter()
            .any(|existing| existing == column)
        {
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

pub fn execution_schema_current(conn: &Connection) -> rusqlite::Result<bool> {
    fn table_exists(conn: &Connection, table: &str) -> rusqlite::Result<bool> {
        Ok(conn
            .query_row(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
                [table],
                |_| Ok(1_i64),
            )
            .optional()?
            .is_some())
    }

    fn has_column(conn: &Connection, table: &str, column: &str) -> rusqlite::Result<bool> {
        if !table_exists(conn, table)? {
            return Ok(false);
        }
        let sql = format!("PRAGMA table_info({table})");
        let mut stmt = conn.prepare(&sql)?;
        let columns = stmt
            .query_map([], |row| row.get::<_, String>(1))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(columns.iter().any(|name| name == column))
    }

    if !table_exists(conn, "execution_receipts")?
        || !has_column(conn, "ingress_provenance", "participant_id")?
        || !table_exists(conn, "execution_authorization_provenance")?
    {
        return Ok(false);
    }

    for column in [
        "principal_provider",
        "principal_subject",
        "capability",
        "resource",
        "source",
        "reason",
        "grant_id",
    ] {
        if !has_column(conn, "execution_authorization_provenance", column)? {
            return Ok(false);
        }
    }
    Ok(true)
}

pub fn get_execution_receipt(
    conn: &Connection,
    participant_id: &str,
    intent_id: &str,
) -> rusqlite::Result<Option<ExecutionReceipt>> {
    conn.query_row(
        "SELECT participant_id, intent_id, intent_hash, capability, message_id, status
         FROM execution_receipts
         WHERE participant_id = ?1 AND intent_id = ?2",
        params![participant_id, intent_id],
        |row| {
            Ok(ExecutionReceipt {
                participant_id: row.get(0)?,
                intent_id: row.get(1)?,
                intent_hash: row.get(2)?,
                capability: row.get(3)?,
                message_id: row.get(4)?,
                status: row.get(5)?,
            })
        },
    )
    .optional()
}

pub fn get_authorization_provenance(
    conn: &Connection,
    participant_id: &str,
    intent_id: &str,
) -> rusqlite::Result<Option<AuthorizationProvenance>> {
    conn.query_row(
        "SELECT participant_id, intent_id, principal_provider, principal_subject,
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
        },
    )
    .optional()
}

pub fn get_execution_audit_bundle(
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

pub fn verify_execution_audit_integrity(
    conn: &Connection,
    participant_id: &str,
    intent_id: &str,
) -> rusqlite::Result<ExecutionAuditIntegrityReport> {
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

    let mut expected_authorization_resource: Option<String> = None;
    let message = conn
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
        expected_authorization_resource = Some(channel.clone());
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

    let navigation_table_exists: i64 = conn.query_row(
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
            }
        }
        _ => violations.push("authorization_provenance_duplicate".to_owned()),
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
                        let grant = conn
                            .query_row(
                                "SELECT participant_id, capability, one_shot,
                                        consumed_at, consumed_intent_id
                                 FROM delegated_grants
                                 WHERE id = ?1",
                                [grant_id],
                                |row| {
                                    Ok((
                                        row.get::<_, String>(0)?,
                                        row.get::<_, String>(1)?,
                                        row.get::<_, i64>(2)?,
                                        row.get::<_, Option<i64>>(3)?,
                                        row.get::<_, Option<String>>(4)?,
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

pub fn sweep_execution_audit_integrity(
    conn: &Connection,
) -> rusqlite::Result<ExecutionAuditSweepReport> {
    let identities = {
        let mut stmt = conn.prepare(
            "SELECT participant_id, intent_id
             FROM execution_receipts
             ORDER BY participant_id, intent_id",
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
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

pub fn execute_message_intent(
    conn: &Connection,
    identity: &Identity,
    request: MessageExecutionRequest<'_>,
) -> rusqlite::Result<MessageExecutionResult> {
    let expected_hash = message_request_hash(
        request.channel,
        request.kind,
        request.body,
        request.intent.conversation_ref.as_deref(),
        request.reply_to,
    );
    if request.authority.principal != request.ingress.principal
        || request.ingress.intent_id != request.intent.intent_id
        || identity.instance != request.intent.participant_id
        || request.intent.capability != POST_MESSAGE_CAPABILITY
        || request.intent.resource != request.channel
        || request.intent.request_hash != expected_hash
    {
        return Err(rusqlite::Error::InvalidQuery);
    }

    let tx = conn.unchecked_transaction()?;
    let authorization_decision = authorization::evaluate_authorization(
        &tx,
        &request.authority.principal,
        &request.intent.participant_id,
        &request.intent.capability,
        Some(&request.intent.resource),
        Some(&request.intent.intent_id),
    )?;
    if !authorization_decision.allowed {
        return Ok(MessageExecutionResult::AuthorizationDenied);
    }
    let consume_grant_id = authorization_decision.consume_grant_id;
    let visibility = if request.channel == "blackboard-lounge" {
        "public"
    } else {
        "private"
    };
    tx.execute(
        "INSERT OR IGNORE INTO channels (name, visibility, status, created_by)
         VALUES (?1, ?2, 'active', ?3)",
        params![request.channel, visibility, identity.instance],
    )?;
    let channel_status: Option<String> = tx
        .query_row(
            "SELECT status FROM channels WHERE name = ?1",
            [request.channel],
            |row| row.get(0),
        )
        .optional()?;
    if channel_status.as_deref() != Some("active") {
        return Ok(MessageExecutionResult::ChannelArchived);
    }

    let reserved = tx.execute(
        "INSERT OR IGNORE INTO navigation_writes
             (instance, nonce, request_hash, message_id)
         VALUES (?1, ?2, ?3, 0)",
        params![
            identity.instance,
            request.intent.intent_id,
            request.intent.request_hash
        ],
    )?;

    let (message, created) = if reserved == 0 {
        let (stored_hash, message_id): (String, i64) = tx.query_row(
            "SELECT request_hash, message_id
             FROM navigation_writes
             WHERE instance = ?1 AND nonce = ?2",
            params![identity.instance, request.intent.intent_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        let message = message_by_id(&tx, message_id)?;
        if stored_hash != request.intent.request_hash
            && (message.channel != request.channel
                || message.kind != request.kind
                || message.body != request.body
                || message.reply_to != request.reply_to
                || message.conversation_ref.as_deref()
                    != request.intent.conversation_ref.as_deref())
        {
            return Ok(MessageExecutionResult::IntentConflict);
        }
        (message, false)
    } else {
        if let Some(target) = request.reply_to {
            let exists = tx
                .query_row("SELECT 1 FROM messages WHERE id = ?1", [target], |_| {
                    Ok(1_i64)
                })
                .optional()?
                .is_some();
            if !exists {
                return Ok(MessageExecutionResult::ReplyTargetNotFound);
            }
        }

        tx.execute(
            "INSERT INTO messages
                (channel, source, instance, conversation_ref, kind, body, reply_to)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                request.channel,
                identity.source,
                identity.instance,
                request.intent.conversation_ref,
                request.kind,
                request.body,
                request.reply_to
            ],
        )?;
        let message_id = tx.last_insert_rowid();
        tx.execute(
            "UPDATE navigation_writes
             SET message_id = ?1
             WHERE instance = ?2 AND nonce = ?3",
            params![message_id, identity.instance, request.intent.intent_id],
        )?;
        (message_by_id(&tx, message_id)?, true)
    };

    let receipt = ExecutionReceipt {
        participant_id: request.intent.participant_id.clone(),
        intent_id: request.intent.intent_id.clone(),
        intent_hash: request.intent.request_hash.clone(),
        capability: request.intent.capability.clone(),
        message_id: message.id,
        status: "committed".to_owned(),
    };
    record_execution_in_tx(&tx, request.ingress, &receipt)?;
    if created {
        record_authorization_provenance_in_tx(
            &tx,
            &receipt,
            &request.authority.principal,
            &request.intent.resource,
            &authorization_decision,
        )?;
        if let Some(grant_id) = consume_grant_id {
            authorization::consume_delegated_grant_in_tx(&tx, grant_id, &request.intent.intent_id)?;
        }
    }
    tx.commit()?;

    Ok(if created {
        MessageExecutionResult::Created(message)
    } else {
        MessageExecutionResult::Existing(message)
    })
}

#[cfg(test)]
pub fn record_execution(
    conn: &Connection,
    ingress: &IngressProvenance,
    receipt: &ExecutionReceipt,
) -> rusqlite::Result<()> {
    migrate_execution_schema(conn)?;
    let tx = conn.unchecked_transaction()?;
    record_execution_in_tx(&tx, ingress, receipt)?;
    tx.commit()
}

fn record_execution_in_tx(
    tx: &Transaction<'_>,
    ingress: &IngressProvenance,
    receipt: &ExecutionReceipt,
) -> rusqlite::Result<()> {
    let existing_delivery: Option<(Option<String>, String, String, String, String, String)> = tx
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
        if existing != expected {
            return Err(rusqlite::Error::InvalidQuery);
        }
    } else {
        tx.execute(
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
    }

    let existing_receipt: Option<(String, String, i64, String)> = tx
        .query_row(
            "SELECT intent_hash, capability, message_id, status
             FROM execution_receipts
             WHERE participant_id = ?1 AND intent_id = ?2",
            params![receipt.participant_id, receipt.intent_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()?;

    if let Some(existing) = existing_receipt {
        let (_, existing_capability, existing_message_id, existing_status) = existing;
        if existing_capability != receipt.capability
            || existing_message_id != receipt.message_id
            || existing_status != receipt.status
        {
            return Err(rusqlite::Error::InvalidQuery);
        }
    } else {
        tx.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                receipt.participant_id,
                receipt.intent_id,
                receipt.intent_hash,
                receipt.capability,
                receipt.message_id,
                receipt.status
            ],
        )?;
    }

    Ok(())
}

fn record_authorization_provenance_in_tx(
    tx: &Transaction<'_>,
    receipt: &ExecutionReceipt,
    principal: &Principal,
    resource: &str,
    decision: &authorization::AuthorizationDecision,
) -> rusqlite::Result<()> {
    if !decision.allowed {
        return Err(rusqlite::Error::InvalidQuery);
    }
    tx.execute(
        "INSERT INTO execution_authorization_provenance
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
        ],
    )?;
    Ok(())
}

fn message_by_id(conn: &Connection, id: i64) -> rusqlite::Result<Message> {
    conn.query_row(
        "SELECT id, created_at, channel, source, instance, conversation_ref, kind, body, reply_to
         FROM messages WHERE id = ?1",
        [id],
        |row| {
            Ok(Message {
                id: row.get(0)?,
                created_at: row.get(1)?,
                channel: row.get(2)?,
                source: row.get(3)?,
                instance: row.get(4)?,
                conversation_ref: row.get(5)?,
                kind: row.get(6)?,
                body: row.get(7)?,
                reply_to: row.get(8)?,
            })
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn test_identity() -> Identity {
        Identity {
            source: "maker".into(),
            instance: "maker-main".into(),
            label: None,
        }
    }

    fn provision_test_participant(conn: &Connection) {
        identity::provision_web_participant_identity(conn, "maker-main", "maker", Some("Maker"))
            .unwrap()
            .unwrap();
    }

    fn test_request_parts(
        intent_id: &str,
    ) -> (AuthorityContext, IntentEnvelope, IngressProvenance) {
        let principal = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        let authority = AuthorityContext {
            principal: principal.clone(),
            mechanism: "hmac-sha256-v1".into(),
        };
        let intent = IntentEnvelope {
            intent_id: intent_id.into(),
            participant_id: "maker-main".into(),
            conversation_ref: None,
            capability: POST_MESSAGE_CAPABILITY.into(),
            resource: "blackboard-lounge".into(),
            request_hash: message_request_hash("blackboard-lounge", "message", "hello", None, None),
        };
        let ingress = IngressProvenance {
            delivery_id: format!("rest:maker-main:{intent_id}"),
            intent_id: intent_id.into(),
            transport: "rest".into(),
            external_ref: intent_id.into(),
            principal,
        };
        (authority, intent, ingress)
    }

    #[test]
    fn github_default_intent_preserves_legacy_nonce() {
        let delivery = github_delivery_id(1364516460, 77);
        let intent = github_intent_id(None, 1364516460, 77).unwrap();
        assert_eq!(delivery, "github:1364516460:issue:77");
        assert_eq!(intent, delivery);
    }

    #[test]
    fn explicit_intent_is_transport_independent() {
        let first = github_intent_id(Some("intent-abc"), 1364516460, 77).unwrap();
        let second = github_intent_id(Some("intent-abc"), 1364516460, 78).unwrap();
        assert_eq!(first, second);
        assert_ne!(
            github_delivery_id(1364516460, 77),
            github_delivery_id(1364516460, 78)
        );
    }

    #[test]
    fn execution_audit_is_idempotent_and_rejects_rebinding() {
        let conn = Connection::open_in_memory().unwrap();
        migrate_execution_schema(&conn).unwrap();

        let ingress = IngressProvenance {
            delivery_id: "github:1:issue:2".into(),
            intent_id: "intent-1".into(),
            transport: "github-webhook".into(),
            external_ref: "github:1:issue:2".into(),
            principal: Principal {
                provider: "github".into(),
                subject: "543608".into(),
            },
        };
        let receipt = ExecutionReceipt {
            participant_id: "maker-main".into(),
            intent_id: "intent-1".into(),
            intent_hash: "hash-a".into(),
            capability: POST_MESSAGE_CAPABILITY.into(),
            message_id: 58,
            status: "committed".into(),
        };

        record_execution(&conn, &ingress, &receipt).unwrap();
        record_execution(&conn, &ingress, &receipt).unwrap();

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM execution_receipts", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 1);

        let mut changed = receipt.clone();
        changed.message_id = 59;
        assert!(record_execution(&conn, &ingress, &changed).is_err());
    }

    #[test]
    fn shared_execution_boundary_deduplicates_across_transports() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        provision_test_participant(&conn);
        let identity = test_identity();
        let (authority, intent, rest_ingress) = test_request_parts("semantic-intent-1");

        let first = execute_message_intent(
            &conn,
            &identity,
            MessageExecutionRequest {
                authority: &authority,
                ingress: &rest_ingress,
                intent: &intent,
                channel: "blackboard-lounge",
                kind: "message",
                body: "hello",
                reply_to: None,
            },
        )
        .unwrap();
        let first_id = match first {
            MessageExecutionResult::Created(message) => message.id,
            other => panic!("unexpected result: {other:?}"),
        };

        let principal = rest_ingress.principal.clone();
        let mcp_ingress = IngressProvenance {
            delivery_id: "mcp:maker-main:nonce-1".into(),
            intent_id: intent.intent_id.clone(),
            transport: "mcp".into(),
            external_ref: "nonce-1".into(),
            principal,
        };
        let second = execute_message_intent(
            &conn,
            &identity,
            MessageExecutionRequest {
                authority: &authority,
                ingress: &mcp_ingress,
                intent: &intent,
                channel: "blackboard-lounge",
                kind: "message",
                body: "hello",
                reply_to: None,
            },
        )
        .unwrap();
        match second {
            MessageExecutionResult::Existing(message) => assert_eq!(message.id, first_id),
            other => panic!("unexpected result: {other:?}"),
        }

        let receipt_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM execution_receipts
                 WHERE participant_id = 'maker-main' AND intent_id = 'semantic-intent-1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(receipt_count, 1);
        let ingress_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM ingress_provenance
                 WHERE intent_id = 'semantic-intent-1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(ingress_count, 2);
        let provenance = get_authorization_provenance(&conn, "maker-main", "semantic-intent-1")
            .unwrap()
            .unwrap();
        assert_eq!(provenance.source, "implicit_authority");
        assert_eq!(provenance.reason, "implicit_participant_hmac");
        assert_eq!(
            provenance.principal,
            Some(Principal {
                provider: "participant-hmac".into(),
                subject: "maker-main".into(),
            })
        );
        assert_eq!(provenance.capability.as_deref(), Some("post_message"));
        assert_eq!(provenance.resource.as_deref(), Some("blackboard-lounge"));
        let provenance_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM execution_authorization_provenance
                 WHERE participant_id = 'maker-main' AND intent_id = 'semantic-intent-1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(provenance_count, 1);
        let audit = get_execution_audit_bundle(&conn, "maker-main", "semantic-intent-1")
            .unwrap()
            .unwrap();
        assert_eq!(audit.receipt.message_id, first_id);
        assert_eq!(audit.ingress.len(), 2);
        assert_eq!(
            audit.authorization.as_ref().map(|v| v.reason.as_str()),
            Some("implicit_participant_hmac")
        );
    }

    #[test]
    fn one_shot_delegated_grant_is_consumed_atomically_and_replays_idempotently() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        provision_test_participant(&conn);
        authorization::ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource, intent_id, expires_at, one_shot)
             VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', 'blackboard-lounge', 'delegated-intent-1', unixepoch() + 3600, 1)",
            [],
        )
        .unwrap();

        let identity = test_identity();
        let principal = Principal {
            provider: "oidc:https://issuer.example".into(),
            subject: "agent-1".into(),
        };
        let authority = AuthorityContext {
            principal: principal.clone(),
            mechanism: "oidc-bearer-jwt".into(),
        };
        let intent = IntentEnvelope {
            intent_id: "delegated-intent-1".into(),
            participant_id: "maker-main".into(),
            conversation_ref: None,
            capability: POST_MESSAGE_CAPABILITY.into(),
            resource: "blackboard-lounge".into(),
            request_hash: message_request_hash(
                "blackboard-lounge",
                "message",
                "delegated",
                None,
                None,
            ),
        };
        let ingress = IngressProvenance {
            delivery_id: "oidc:agent-1:delivery-1".into(),
            intent_id: intent.intent_id.clone(),
            transport: "oidc-http".into(),
            external_ref: "delivery-1".into(),
            principal: principal.clone(),
        };

        let first = execute_message_intent(
            &conn,
            &identity,
            MessageExecutionRequest {
                authority: &authority,
                ingress: &ingress,
                intent: &intent,
                channel: "blackboard-lounge",
                kind: "message",
                body: "delegated",
                reply_to: None,
            },
        )
        .unwrap();
        let first_id = match first {
            MessageExecutionResult::Created(message) => message.id,
            other => panic!("unexpected result: {other:?}"),
        };

        let consumed: (Option<i64>, Option<String>) = conn
            .query_row(
                "SELECT consumed_at, consumed_intent_id FROM delegated_grants WHERE principal_subject = 'agent-1'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert!(consumed.0.is_some());
        assert_eq!(consumed.1.as_deref(), Some("delegated-intent-1"));

        let provenance = get_authorization_provenance(&conn, "maker-main", "delegated-intent-1")
            .unwrap()
            .unwrap();
        assert_eq!(provenance.source, "delegated_grants");
        assert_eq!(provenance.reason, "delegated_grant_match");
        assert!(provenance.grant_id.is_some());
        assert_eq!(provenance.principal, Some(authority.principal.clone()));
        assert_eq!(provenance.capability.as_deref(), Some("post_message"));
        assert_eq!(provenance.resource.as_deref(), Some("blackboard-lounge"));
        conn.execute(
            "UPDATE delegated_grants SET status = 'inactive' WHERE principal_subject = 'agent-1'",
            [],
        )
        .unwrap();
        let stable = get_authorization_provenance(&conn, "maker-main", "delegated-intent-1")
            .unwrap()
            .unwrap();
        assert_eq!(stable, provenance);
        conn.execute(
            "UPDATE delegated_grants SET status = 'active' WHERE principal_subject = 'agent-1'",
            [],
        )
        .unwrap();

        let replay_ingress = IngressProvenance {
            delivery_id: "oidc:agent-1:delivery-2".into(),
            intent_id: intent.intent_id.clone(),
            transport: "oidc-http".into(),
            external_ref: "delivery-2".into(),
            principal,
        };
        let replay = execute_message_intent(
            &conn,
            &identity,
            MessageExecutionRequest {
                authority: &authority,
                ingress: &replay_ingress,
                intent: &intent,
                channel: "blackboard-lounge",
                kind: "message",
                body: "delegated",
                reply_to: None,
            },
        )
        .unwrap();
        match replay {
            MessageExecutionResult::Existing(message) => assert_eq!(message.id, first_id),
            other => panic!("unexpected replay result: {other:?}"),
        }

        let other_intent = IntentEnvelope {
            intent_id: "delegated-intent-2".into(),
            participant_id: "maker-main".into(),
            conversation_ref: None,
            capability: POST_MESSAGE_CAPABILITY.into(),
            resource: "blackboard-lounge".into(),
            request_hash: message_request_hash(
                "blackboard-lounge",
                "message",
                "second",
                None,
                None,
            ),
        };
        let other_ingress = IngressProvenance {
            delivery_id: "oidc:agent-1:delivery-3".into(),
            intent_id: other_intent.intent_id.clone(),
            transport: "oidc-http".into(),
            external_ref: "delivery-3".into(),
            principal: authority.principal.clone(),
        };
        assert!(matches!(
            execute_message_intent(
                &conn,
                &identity,
                MessageExecutionRequest {
                    authority: &authority,
                    ingress: &other_ingress,
                    intent: &other_intent,
                    channel: "blackboard-lounge",
                    kind: "message",
                    body: "second",
                    reply_to: None,
                },
            )
            .unwrap(),
            MessageExecutionResult::AuthorizationDenied
        ));
    }

    #[test]
    fn failed_execution_does_not_consume_one_shot_delegated_grant() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        provision_test_participant(&conn);
        authorization::ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource, intent_id, expires_at, one_shot)
             VALUES ('oidc:https://issuer.example', 'agent-2', 'maker-main', 'post_message', 'blackboard-lounge', 'delegated-fail-1', unixepoch() + 3600, 1)",
            [],
        )
        .unwrap();
        let identity = test_identity();
        let principal = Principal {
            provider: "oidc:https://issuer.example".into(),
            subject: "agent-2".into(),
        };
        let authority = AuthorityContext {
            principal: principal.clone(),
            mechanism: "oidc-bearer-jwt".into(),
        };
        let intent = IntentEnvelope {
            intent_id: "delegated-fail-1".into(),
            participant_id: "maker-main".into(),
            conversation_ref: None,
            capability: POST_MESSAGE_CAPABILITY.into(),
            resource: "blackboard-lounge".into(),
            request_hash: message_request_hash(
                "blackboard-lounge",
                "message",
                "will-fail",
                None,
                Some(999999),
            ),
        };
        let ingress = IngressProvenance {
            delivery_id: "oidc:agent-2:delivery-1".into(),
            intent_id: intent.intent_id.clone(),
            transport: "oidc-http".into(),
            external_ref: "delivery-1".into(),
            principal,
        };
        assert!(matches!(
            execute_message_intent(
                &conn,
                &identity,
                MessageExecutionRequest {
                    authority: &authority,
                    ingress: &ingress,
                    intent: &intent,
                    channel: "blackboard-lounge",
                    kind: "message",
                    body: "will-fail",
                    reply_to: Some(999999),
                },
            )
            .unwrap(),
            MessageExecutionResult::ReplyTargetNotFound
        ));
        let consumed_at: Option<i64> = conn
            .query_row(
                "SELECT consumed_at FROM delegated_grants WHERE principal_subject = 'agent-2'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(consumed_at.is_none());
        assert!(
            get_authorization_provenance(&conn, "maker-main", "delegated-fail-1",)
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn audit_failure_rolls_back_message_navigation_and_receipt() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        provision_test_participant(&conn);
        migrate_execution_schema(&conn).unwrap();

        conn.execute(
            "INSERT INTO ingress_provenance
                (delivery_id, intent_id, transport, external_ref, principal_provider, principal_subject)
             VALUES ('rest:maker-main:atomic-intent', 'different-intent', 'rest', 'old', 'hmac', 'maker-main')",
            [],
        )
        .unwrap();

        let identity = test_identity();
        let (authority, intent, ingress) = test_request_parts("atomic-intent");
        let result = execute_message_intent(
            &conn,
            &identity,
            MessageExecutionRequest {
                authority: &authority,
                ingress: &ingress,
                intent: &intent,
                channel: "blackboard-lounge",
                kind: "message",
                body: "hello",
                reply_to: None,
            },
        );
        assert!(result.is_err());

        let message_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM messages WHERE instance = 'maker-main' AND body = 'hello'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let navigation_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM navigation_writes
                 WHERE instance = 'maker-main' AND nonce = 'atomic-intent'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let receipt_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM execution_receipts
                 WHERE participant_id = 'maker-main' AND intent_id = 'atomic-intent'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(message_count, 0);
        assert_eq!(navigation_count, 0);
        assert_eq!(receipt_count, 0);
        let authorization_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM execution_authorization_provenance
                 WHERE participant_id = 'maker-main' AND intent_id = 'atomic-intent'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(authorization_count, 0);
    }
}
