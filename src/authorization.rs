use std::collections::{HashMap, HashSet};

use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde::Serialize;

use crate::{execution, execution::Principal, identity};

pub const READ_MESSAGES: &str = "read_messages";
pub const POST_MESSAGE: &str = "post_message";
pub const REPLY: &str = "reply";
pub const READ_EXECUTION_RECEIPT: &str = "read_execution_receipt";
pub const READ_EXECUTION_AUDIT: &str = "read_execution_audit";
pub const READ_EXECUTION_AUDIT_SWEEP: &str = "read_execution_audit_sweep";
pub const EXECUTION_AUDIT_SWEEP_RESOURCE: &str = "execution-audit-sweep";
pub const READ_AUTHORIZATION_POLICY_INTEGRITY: &str = "read_authorization_policy_integrity";
pub const AUTHORIZATION_POLICY_INTEGRITY_RESOURCE: &str = "authorization-policy-integrity";
pub const READ_AUTHORIZATION_POLICY: &str = "read_authorization_policy";
pub const AUTHORIZATION_POLICY_RESOURCE: &str = "authorization-policy";
pub const READ_AUTHORIZATION_DECISION: &str = "read_authorization_decision";
pub const AUTHORIZATION_DECISION_RESOURCE: &str = "authorization-decision";
pub const MANAGE_AUTHORIZATION_POLICY: &str = "manage_authorization_policy";
pub const AUTHORIZATION_POLICY_ADMIN_RESOURCE: &str = "authorization-policy-administration";
pub const READ_AUTHORIZATION_ADMINISTRATION_HISTORY: &str =
    "read_authorization_administration_history";
pub const AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE: &str =
    "authorization-administration-history";
pub const MANAGE_CHANNELS: &str = "manage_channels";

const KNOWN_CAPABILITIES: [&str; 12] = [
    READ_MESSAGES,
    POST_MESSAGE,
    REPLY,
    READ_EXECUTION_RECEIPT,
    READ_EXECUTION_AUDIT,
    READ_EXECUTION_AUDIT_SWEEP,
    READ_AUTHORIZATION_POLICY_INTEGRITY,
    READ_AUTHORIZATION_POLICY,
    READ_AUTHORIZATION_DECISION,
    MANAGE_AUTHORIZATION_POLICY,
    READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
    MANAGE_CHANNELS,
];

pub fn is_known_capability(capability: &str) -> bool {
    KNOWN_CAPABILITIES.contains(&capability)
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct EffectiveGrant {
    pub capability: String,
    pub resource: Option<String>,
    pub origin: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationDecision {
    pub allowed: bool,
    pub source: &'static str,
    pub reason: &'static str,
    pub grant_id: Option<i64>,
    pub consume_grant_id: Option<i64>,
}

impl AuthorizationDecision {
    fn allow(
        source: &'static str,
        reason: &'static str,
        grant_id: Option<i64>,
        consume_grant_id: Option<i64>,
    ) -> Self {
        Self {
            allowed: true,
            source,
            reason,
            grant_id,
            consume_grant_id,
        }
    }

    fn deny(source: &'static str, reason: &'static str, grant_id: Option<i64>) -> Self {
        Self {
            allowed: false,
            source,
            reason,
            grant_id,
            consume_grant_id: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorizationDecisionTarget {
    pub principal: Principal,
    pub participant_id: String,
    pub capability: String,
    pub resource: Option<String>,
    pub intent_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationDecisionExplanation {
    pub allowed: bool,
    pub source: &'static str,
    pub reason: &'static str,
    pub grant_id: Option<i64>,
    pub consume_on_commit: bool,
}

impl From<AuthorizationDecision> for AuthorizationDecisionExplanation {
    fn from(decision: AuthorizationDecision) -> Self {
        Self {
            allowed: decision.allowed,
            source: decision.source,
            reason: decision.reason,
            grant_id: decision.grant_id,
            consume_on_commit: decision.consume_grant_id.is_some(),
        }
    }
}

pub fn normalize_authorization_decision_target(
    principal_provider: &str,
    principal_subject: &str,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: Option<&str>,
) -> Result<AuthorizationDecisionTarget, &'static str> {
    fn normalize(value: &str, max_bytes: usize) -> Option<String> {
        let value = value.trim();
        (!value.is_empty() && value.len() <= max_bytes && !value.chars().any(char::is_control))
            .then(|| value.to_owned())
    }

    let principal_provider =
        normalize(principal_provider, 256).ok_or("invalid_principal_provider")?;
    let principal_subject = normalize(principal_subject, 256).ok_or("invalid_principal_subject")?;
    let participant_id =
        identity::validate_participant_id(participant_id).ok_or("invalid_participant_id")?;
    let capability = normalize(capability, 128).ok_or("invalid_capability")?;
    if !is_known_capability(&capability) {
        return Err("unsupported_capability");
    }
    let resource = resource
        .map(|value| normalize(value, 256).ok_or("invalid_resource"))
        .transpose()?;
    let intent_id = intent_id
        .map(|value| execution::normalize_intent_id(value).map_err(|_| "invalid_intent_id"))
        .transpose()?;

    Ok(AuthorizationDecisionTarget {
        principal: Principal {
            provider: principal_provider,
            subject: principal_subject,
        },
        participant_id,
        capability,
        resource,
        intent_id,
    })
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
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

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct DurableGrantSnapshot {
    pub id: i64,
    pub principal_provider: String,
    pub principal_subject: String,
    pub participant_id: String,
    pub capability: String,
    pub resource: Option<String>,
    pub status: String,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct DelegatedGrantSnapshot {
    pub id: i64,
    pub principal_provider: String,
    pub principal_subject: String,
    pub participant_id: String,
    pub capability: String,
    pub resource: Option<String>,
    pub intent_id: Option<String>,
    pub expires_at: Option<i64>,
    pub one_shot: bool,
    pub consumed_at: Option<i64>,
    pub consumed_intent_id: Option<String>,
    pub status: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationPolicySnapshot {
    pub durable_grants: Vec<DurableGrantSnapshot>,
    pub delegated_grants: Vec<DelegatedGrantSnapshot>,
}

#[derive(Debug, Clone)]
struct ParticipantPolicyRow {
    status: String,
    role: String,
    owner_provider: Option<String>,
    owner_subject: Option<String>,
}

pub fn ensure_grant_schema(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS principal_grants (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            principal_provider  TEXT NOT NULL,
            principal_subject   TEXT NOT NULL,
            participant_id      TEXT NOT NULL,
            capability          TEXT NOT NULL,
            resource            TEXT,
            status              TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'inactive')),
            created_at          INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at          INTEGER NOT NULL DEFAULT (unixepoch()),
            UNIQUE (
                principal_provider,
                principal_subject,
                participant_id,
                capability,
                resource
            )
        );
        CREATE INDEX IF NOT EXISTS idx_principal_grants_lookup
            ON principal_grants (
                principal_provider,
                principal_subject,
                participant_id,
                capability,
                status
            );

        CREATE TABLE IF NOT EXISTS delegated_grants (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            principal_provider  TEXT NOT NULL,
            principal_subject   TEXT NOT NULL,
            participant_id      TEXT NOT NULL,
            capability          TEXT NOT NULL,
            resource            TEXT,
            intent_id           TEXT,
            expires_at          INTEGER,
            one_shot            INTEGER NOT NULL DEFAULT 0 CHECK (one_shot IN (0, 1)),
            consumed_at         INTEGER,
            consumed_intent_id  TEXT,
            status              TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'inactive')),
            created_at          INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at          INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_delegated_grants_lookup
            ON delegated_grants (
                principal_provider,
                principal_subject,
                participant_id,
                capability,
                status
            );",
    )
}

pub fn authorization_policy_snapshot_schema_current(conn: &Connection) -> rusqlite::Result<bool> {
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
            "created_at",
            "updated_at",
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
    )?)
}

pub fn read_authorization_policy_snapshot(
    conn: &Connection,
) -> rusqlite::Result<AuthorizationPolicySnapshot> {
    if !authorization_policy_snapshot_schema_current(conn)? {
        return Err(rusqlite::Error::InvalidQuery);
    }

    let durable_grants = {
        let mut stmt = conn.prepare(
            "SELECT id, principal_provider, principal_subject, participant_id, capability,
                    resource, status, created_at, updated_at
             FROM principal_grants
             ORDER BY id",
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok(DurableGrantSnapshot {
                    id: row.get(0)?,
                    principal_provider: row.get(1)?,
                    principal_subject: row.get(2)?,
                    participant_id: row.get(3)?,
                    capability: row.get(4)?,
                    resource: row.get(5)?,
                    status: row.get(6)?,
                    created_at: row.get(7)?,
                    updated_at: row.get(8)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };

    let delegated_grants = {
        let mut stmt = conn.prepare(
            "SELECT id, principal_provider, principal_subject, participant_id, capability,
                    resource, intent_id, expires_at, one_shot, consumed_at,
                    consumed_intent_id, status
             FROM delegated_grants
             ORDER BY id",
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok(DelegatedGrantSnapshot {
                    id: row.get(0)?,
                    principal_provider: row.get(1)?,
                    principal_subject: row.get(2)?,
                    participant_id: row.get(3)?,
                    capability: row.get(4)?,
                    resource: row.get(5)?,
                    intent_id: row.get(6)?,
                    expires_at: row.get(7)?,
                    one_shot: row.get::<_, i64>(8)? != 0,
                    consumed_at: row.get(9)?,
                    consumed_intent_id: row.get(10)?,
                    status: row.get(11)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };

    Ok(AuthorizationPolicySnapshot {
        durable_grants,
        delegated_grants,
    })
}

pub fn authorization_integrity_schema_current(conn: &Connection) -> rusqlite::Result<bool> {
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
        let rows = stmt
            .query_map([], |row| row.get::<_, String>(0))?
            .collect::<rusqlite::Result<HashSet<_>>>()?;
        rows
    };
    let mut violations = Vec::new();
    type DurableScopeKey = (String, String, String, String, Option<String>);
    let mut durable_scopes: HashMap<DurableScopeKey, Vec<i64>> = HashMap::new();

    let durable_rows = {
        let mut stmt = conn.prepare(
            "SELECT id, principal_provider, principal_subject, participant_id, capability,
                    resource, status
             FROM principal_grants
             ORDER BY id",
        )?;
        let rows = stmt
            .query_map([], |row| {
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
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
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
            let id_list = ids.iter().map(i64::to_string).collect::<Vec<_>>().join(",");
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
        let rows = stmt
            .query_map([], |row| {
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
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
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
        if let (Some(bound), Some(consumed)) = (intent_id.as_deref(), consumed_intent_id.as_deref())
        {
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

fn table_has_columns(conn: &Connection, table: &str, required: &[&str]) -> rusqlite::Result<bool> {
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

pub fn authorization_decision_schema_current(conn: &Connection) -> rusqlite::Result<bool> {
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
    )? && table_has_columns(
        conn,
        "web_participants",
        &[
            "participant_id",
            "status",
            "role",
            "owner_provider",
            "owner_subject",
        ],
    )? && table_has_columns(
        conn,
        "execution_receipts",
        &["participant_id", "intent_id", "status"],
    )?)
}

pub fn explain_authorization(
    conn: &Connection,
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: Option<&str>,
) -> rusqlite::Result<AuthorizationDecision> {
    if !authorization_decision_schema_current(conn)? {
        return Err(rusqlite::Error::InvalidQuery);
    }
    evaluate_authorization_current_schema(
        conn,
        principal,
        participant_id,
        capability,
        resource,
        intent_id,
    )
}

pub fn evaluate_authorization(
    conn: &Connection,
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: Option<&str>,
) -> rusqlite::Result<AuthorizationDecision> {
    ensure_grant_schema(conn)?;
    evaluate_authorization_current_schema(
        conn,
        principal,
        participant_id,
        capability,
        resource,
        intent_id,
    )
}

fn evaluate_authorization_current_schema(
    conn: &Connection,
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: Option<&str>,
) -> rusqlite::Result<AuthorizationDecision> {
    let Some(participant) = participant_policy_row(conn, participant_id)? else {
        return Ok(AuthorizationDecision::deny(
            "participant_lifecycle",
            "participant_missing",
            None,
        ));
    };
    if participant.status != "active" {
        return Ok(AuthorizationDecision::deny(
            "participant_lifecycle",
            "participant_inactive",
            None,
        ));
    }

    let explicit_count: i64 = conn.query_row(
        "SELECT COUNT(*)
         FROM principal_grants
         WHERE principal_provider = ?1
           AND principal_subject = ?2
           AND participant_id = ?3
           AND capability = ?4
           AND status = 'active'",
        params![
            principal.provider,
            principal.subject,
            participant_id,
            capability
        ],
        |row| row.get(0),
    )?;

    if explicit_count > 0 {
        let explicit_match: Option<i64> = conn
            .query_row(
                "SELECT id
                 FROM principal_grants
                 WHERE principal_provider = ?1
                   AND principal_subject = ?2
                   AND participant_id = ?3
                   AND capability = ?4
                   AND status = 'active'
                   AND (resource IS NULL OR resource = ?5)
                 ORDER BY (resource IS NOT NULL) DESC, id ASC
                 LIMIT 1",
                params![
                    principal.provider,
                    principal.subject,
                    participant_id,
                    capability,
                    resource
                ],
                |row| row.get(0),
            )
            .optional()?;
        if let Some(grant_id) = explicit_match {
            return Ok(AuthorizationDecision::allow(
                "principal_grants",
                "explicit_durable_grant_match",
                Some(grant_id),
                None,
            ));
        }
    } else if let Some(reason) = implicit_authority_reason(
        principal,
        participant_id,
        capability,
        resource,
        &participant,
    ) {
        return Ok(AuthorizationDecision::allow(
            "implicit_authority",
            reason,
            None,
            None,
        ));
    }

    if let Some(intent_id) = intent_id {
        let now: i64 = conn.query_row("SELECT unixepoch()", [], |row| row.get(0))?;
        let mut stmt = conn.prepare(
            "SELECT id, resource, intent_id, expires_at, one_shot, consumed_at, consumed_intent_id
             FROM delegated_grants
             WHERE principal_provider = ?1
               AND principal_subject = ?2
               AND participant_id = ?3
               AND capability = ?4
               AND status = 'active'
             ORDER BY
               (intent_id IS NOT NULL) DESC,
               (resource IS NOT NULL) DESC,
               one_shot DESC,
               id ASC",
        )?;
        let rows = stmt
            .query_map(
                params![
                    principal.provider,
                    principal.subject,
                    participant_id,
                    capability
                ],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, Option<String>>(1)?,
                        row.get::<_, Option<String>>(2)?,
                        row.get::<_, Option<i64>>(3)?,
                        row.get::<_, i64>(4)? != 0,
                        row.get::<_, Option<i64>>(5)?,
                        row.get::<_, Option<String>>(6)?,
                    ))
                },
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;

        let mut first_denial: Option<AuthorizationDecision> = None;
        for (
            grant_id,
            grant_resource,
            grant_intent,
            expires_at,
            one_shot,
            consumed_at,
            consumed_intent_id,
        ) in rows
        {
            let denial = if grant_resource
                .as_deref()
                .is_some_and(|value| Some(value) != resource)
            {
                Some("delegated_resource_mismatch")
            } else if grant_intent
                .as_deref()
                .is_some_and(|value| value != intent_id)
            {
                Some("delegated_intent_mismatch")
            } else if one_shot
                && consumed_at.is_some()
                && consumed_intent_id.as_deref() != Some(intent_id)
            {
                Some("delegated_consumed_different_intent")
            } else if expires_at.is_some_and(|value| value <= now)
                && !(one_shot && consumed_intent_id.as_deref() == Some(intent_id))
            {
                Some("delegated_grant_expired")
            } else {
                None
            };

            if let Some(reason) = denial {
                first_denial.get_or_insert_with(|| {
                    AuthorizationDecision::deny("delegated_grants", reason, Some(grant_id))
                });
                continue;
            }

            if one_shot && consumed_at.is_some() {
                if committed_receipt_exists(conn, participant_id, intent_id)? {
                    return Ok(AuthorizationDecision::allow(
                        "delegated_grants",
                        "delegated_committed_replay",
                        Some(grant_id),
                        None,
                    ));
                }
                return Ok(AuthorizationDecision::deny(
                    "delegated_grants",
                    "delegated_consumed_without_committed_receipt",
                    Some(grant_id),
                ));
            }

            return Ok(AuthorizationDecision::allow(
                "delegated_grants",
                "delegated_grant_match",
                Some(grant_id),
                one_shot.then_some(grant_id),
            ));
        }

        if let Some(decision) = first_denial {
            return Ok(decision);
        }
    }

    if explicit_count > 0 {
        return Ok(AuthorizationDecision::deny(
            "principal_grants",
            "explicit_resource_scope_mismatch",
            None,
        ));
    }

    Ok(AuthorizationDecision::deny(
        "policy",
        "no_matching_authority",
        None,
    ))
}

pub fn authorize(
    conn: &Connection,
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
) -> rusqlite::Result<bool> {
    Ok(
        evaluate_authorization(conn, principal, participant_id, capability, resource, None)?
            .allowed,
    )
}

pub fn consume_delegated_grant_in_tx(
    tx: &Transaction<'_>,
    grant_id: i64,
    intent_id: &str,
) -> rusqlite::Result<()> {
    let changed = tx.execute(
        "UPDATE delegated_grants
         SET consumed_at = unixepoch(), consumed_intent_id = ?2, updated_at = unixepoch()
         WHERE id = ?1
           AND status = 'active'
           AND one_shot = 1
           AND consumed_at IS NULL",
        params![grant_id, intent_id],
    )?;
    if changed != 1 {
        return Err(rusqlite::Error::InvalidQuery);
    }
    Ok(())
}

pub fn effective_grants(
    conn: &Connection,
    principal: &Principal,
    participant_id: &str,
) -> rusqlite::Result<Vec<EffectiveGrant>> {
    ensure_grant_schema(conn)?;
    let Some(participant) = participant_policy_row(conn, participant_id)? else {
        return Ok(Vec::new());
    };
    if participant.status != "active" {
        return Ok(Vec::new());
    }

    let mut stmt = conn.prepare(
        "SELECT capability, resource
         FROM principal_grants
         WHERE principal_provider = ?1
           AND principal_subject = ?2
           AND participant_id = ?3
           AND status = 'active'
         ORDER BY capability, resource",
    )?;
    let explicit = stmt
        .query_map(
            params![principal.provider, principal.subject, participant_id],
            |row| {
                Ok(EffectiveGrant {
                    capability: row.get(0)?,
                    resource: row.get(1)?,
                    origin: "explicit".to_owned(),
                })
            },
        )?
        .collect::<rusqlite::Result<Vec<_>>>()?;

    let mut grants = explicit.clone();
    for capability in KNOWN_CAPABILITIES {
        if explicit.iter().any(|grant| grant.capability == capability) {
            continue;
        }
        let implicit_resource = match capability {
            READ_EXECUTION_AUDIT_SWEEP => Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            READ_AUTHORIZATION_POLICY_INTEGRITY => Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            READ_AUTHORIZATION_POLICY => Some(AUTHORIZATION_POLICY_RESOURCE),
            READ_AUTHORIZATION_DECISION => Some(AUTHORIZATION_DECISION_RESOURCE),
            MANAGE_AUTHORIZATION_POLICY => Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
            READ_AUTHORIZATION_ADMINISTRATION_HISTORY => {
                Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE)
            }
            _ => None,
        };
        if authorize(
            conn,
            principal,
            participant_id,
            capability,
            implicit_resource,
        )? {
            grants.push(EffectiveGrant {
                capability: capability.to_owned(),
                resource: implicit_resource.map(str::to_owned),
                origin: "implicit".to_owned(),
            });
        }
    }
    grants.sort_by(|left, right| {
        left.capability
            .cmp(&right.capability)
            .then_with(|| left.resource.cmp(&right.resource))
    });
    Ok(grants)
}

fn participant_policy_row(
    conn: &Connection,
    participant_id: &str,
) -> rusqlite::Result<Option<ParticipantPolicyRow>> {
    conn.query_row(
        "SELECT status, role, owner_provider, owner_subject
         FROM web_participants
         WHERE participant_id = ?1
         LIMIT 1",
        [participant_id],
        |row| {
            Ok(ParticipantPolicyRow {
                status: row.get(0)?,
                role: row.get(1)?,
                owner_provider: row.get(2)?,
                owner_subject: row.get(3)?,
            })
        },
    )
    .optional()
}

fn implicit_authority_reason(
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    participant: &ParticipantPolicyRow,
) -> Option<&'static str> {
    let self_authenticated = matches!(
        principal.provider.as_str(),
        "participant-hmac" | "human-web"
    ) && principal.subject == participant_id;
    if self_authenticated {
        if capability == READ_AUTHORIZATION_POLICY {
            return (principal.provider == "human-web"
                && participant.role == "admin"
                && resource == Some(AUTHORIZATION_POLICY_RESOURCE))
            .then_some("implicit_human_web_admin_authorization_policy");
        }
        if capability == READ_AUTHORIZATION_POLICY_INTEGRITY {
            return (principal.provider == "human-web"
                && participant.role == "admin"
                && resource == Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE))
            .then_some("implicit_human_web_admin_authorization_policy_integrity");
        }
        if capability == READ_AUTHORIZATION_DECISION {
            return (principal.provider == "human-web"
                && participant.role == "admin"
                && resource == Some(AUTHORIZATION_DECISION_RESOURCE))
            .then_some("implicit_human_web_admin_authorization_decision");
        }
        if capability == MANAGE_AUTHORIZATION_POLICY {
            return (principal.provider == "human-web"
                && participant.role == "admin"
                && resource == Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE))
            .then_some("implicit_human_web_admin_authorization_administration");
        }
        if capability == READ_AUTHORIZATION_ADMINISTRATION_HISTORY {
            return (principal.provider == "human-web"
                && participant.role == "admin"
                && resource == Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE))
            .then_some("implicit_human_web_admin_authorization_administration_history");
        }
        if capability == READ_EXECUTION_AUDIT_SWEEP {
            return (principal.provider == "human-web"
                && participant.role == "admin"
                && resource == Some(EXECUTION_AUDIT_SWEEP_RESOURCE))
            .then_some("implicit_human_web_admin_audit_sweep");
        }
        if capability == MANAGE_CHANNELS {
            return (principal.provider == "human-web" && participant.role == "admin")
                .then_some("implicit_human_web_admin");
        }
        if matches!(
            capability,
            READ_MESSAGES | POST_MESSAGE | REPLY | READ_EXECUTION_RECEIPT | READ_EXECUTION_AUDIT
        ) {
            return Some(if principal.provider == "participant-hmac" {
                "implicit_participant_hmac"
            } else {
                "implicit_human_web"
            });
        }
        return None;
    }

    let github_owner = principal.provider == "github"
        && participant.owner_provider.as_deref() == Some("github")
        && participant.owner_subject.as_deref() == Some(principal.subject.as_str());
    (github_owner
        && matches!(
            capability,
            READ_MESSAGES | POST_MESSAGE | REPLY | READ_EXECUTION_RECEIPT | READ_EXECUTION_AUDIT
        ))
    .then_some("implicit_github_owner")
}

fn committed_receipt_exists(
    conn: &Connection,
    participant_id: &str,
    intent_id: &str,
) -> rusqlite::Result<bool> {
    let table_exists: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master
         WHERE type = 'table' AND name = 'execution_receipts'",
        [],
        |row| row.get(0),
    )?;
    if table_exists == 0 {
        return Ok(false);
    }
    let committed: i64 = conn.query_row(
        "SELECT COUNT(*) FROM execution_receipts
         WHERE participant_id = ?1 AND intent_id = ?2 AND status = 'committed'",
        params![participant_id, intent_id],
        |row| row.get(0),
    )?;
    Ok(committed > 0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{db, execution, identity};
    use tempfile::tempdir;

    fn setup() -> (tempfile::TempDir, Connection) {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        identity::provision_web_participant_identity(&conn, "maker-main", "maker", Some("Maker"))
            .unwrap()
            .unwrap();
        conn.execute(
            "UPDATE web_participants
             SET owner_provider = 'github', owner_subject = '543608'
             WHERE participant_id = 'maker-main'",
            [],
        )
        .unwrap();
        (dir, conn)
    }

    #[test]
    fn authorization_decision_explain_refuses_legacy_schema_without_mutation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("legacy.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                owner_provider TEXT,
                owner_subject TEXT
            );
            INSERT INTO web_participants (participant_id, status, role)
            VALUES ('maker-main', 'active', 'admin');",
        )
        .unwrap();
        let before: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let principal = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "agent-1".to_owned(),
        };
        assert!(!authorization_decision_schema_current(&conn).unwrap());
        assert!(explain_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            None,
        )
        .is_err());
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
    fn authorization_decision_explain_reuses_kernel_without_consuming_one_shot() {
        let (_dir, conn) = setup();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, expires_at, one_shot)
             VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message',
                     'control-systems', 'intent-106', unixepoch() + 3600, 1)",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "agent-1".to_owned(),
        };
        let explained = explain_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            Some("intent-106"),
        )
        .unwrap();
        let executable = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            Some("intent-106"),
        )
        .unwrap();
        assert_eq!(explained, executable);
        assert!(explained.allowed);
        assert!(explained.consume_grant_id.is_some());
        let consumed_at: Option<i64> = conn
            .query_row(
                "SELECT consumed_at FROM delegated_grants WHERE intent_id = 'intent-106'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(consumed_at.is_none());
    }

    #[test]
    fn authorization_administration_authority_is_human_web_admin_only_and_isolated() {
        let (_dir, conn) = setup();
        let human = Principal {
            provider: "human-web".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let hmac = Principal {
            provider: "participant-hmac".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let github = Principal {
            provider: "github".to_owned(),
            subject: "543608".to_owned(),
        };
        let oidc = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "admin-agent".to_owned(),
        };

        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
        identity::set_web_participant_role(&conn, "maker-main", "admin").unwrap();
        assert!(authorize(
            &conn,
            &human,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
        for principal in [&hmac, &github, &oidc] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                MANAGE_AUTHORIZATION_POLICY,
                Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
            )
            .unwrap());
        }

        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                &oidc.provider,
                &oidc.subject,
                MANAGE_AUTHORIZATION_POLICY,
                AUTHORIZATION_POLICY_ADMIN_RESOURCE
            ],
        )
        .unwrap();
        assert!(authorize(
            &conn,
            &oidc,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
        for (capability, resource) in [
            (
                READ_AUTHORIZATION_POLICY,
                Some(AUTHORIZATION_POLICY_RESOURCE),
            ),
            (
                READ_AUTHORIZATION_POLICY_INTEGRITY,
                Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            ),
            (
                READ_AUTHORIZATION_DECISION,
                Some(AUTHORIZATION_DECISION_RESOURCE),
            ),
            (
                READ_EXECUTION_AUDIT_SWEEP,
                Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            ),
            (MANAGE_CHANNELS, None),
        ] {
            assert!(!authorize(&conn, &oidc, "maker-main", capability, resource).unwrap());
        }
    }

    #[test]
    fn authorization_administration_history_authority_is_narrow_and_bidirectionally_isolated() {
        let (_dir, conn) = setup();
        let human = Principal {
            provider: "human-web".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let hmac = Principal {
            provider: "participant-hmac".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let github = Principal {
            provider: "github".to_owned(),
            subject: "543608".to_owned(),
        };
        let administrator = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "policy-admin".to_owned(),
        };
        let history_reader = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "history-reader".to_owned(),
        };

        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
        )
        .unwrap());
        identity::set_web_participant_role(&conn, "maker-main", "admin").unwrap();
        assert!(authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
        )
        .unwrap());
        for principal in [&hmac, &github, &administrator, &history_reader] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
                Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
            )
            .unwrap());
        }

        let human_grants = effective_grants(&conn, &human, "maker-main").unwrap();
        assert!(human_grants.iter().any(|grant| {
            grant.capability == READ_AUTHORIZATION_ADMINISTRATION_HISTORY
                && grant.resource.as_deref() == Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE)
                && grant.origin == "implicit"
        }));

        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                &administrator.provider,
                &administrator.subject,
                MANAGE_AUTHORIZATION_POLICY,
                AUTHORIZATION_POLICY_ADMIN_RESOURCE,
            ],
        )
        .unwrap();
        assert!(authorize(
            &conn,
            &administrator,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &administrator,
            "maker-main",
            READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
        )
        .unwrap());

        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                &history_reader.provider,
                &history_reader.subject,
                READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
                AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE,
            ],
        )
        .unwrap();
        assert!(authorize(
            &conn,
            &history_reader,
            "maker-main",
            READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &history_reader,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
    }

    #[test]
    fn authorization_decision_implicit_authority_is_human_web_admin_only() {
        let (_dir, conn) = setup();
        let human = Principal {
            provider: "human-web".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let hmac = Principal {
            provider: "participant-hmac".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let github = Principal {
            provider: "github".to_owned(),
            subject: "543608".to_owned(),
        };
        let oidc = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "agent-1".to_owned(),
        };

        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_DECISION,
            Some(AUTHORIZATION_DECISION_RESOURCE),
        )
        .unwrap());
        identity::set_web_participant_role(&conn, "maker-main", "admin").unwrap();
        assert!(authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_DECISION,
            Some(AUTHORIZATION_DECISION_RESOURCE),
        )
        .unwrap());
        for principal in [&hmac, &github, &oidc] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                READ_AUTHORIZATION_DECISION,
                Some(AUTHORIZATION_DECISION_RESOURCE),
            )
            .unwrap());
        }
    }

    #[test]
    fn authorization_decision_authority_is_isolated_from_other_privileged_capabilities() {
        let (_dir, conn) = setup();
        let decision_reader = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "decision-reader".to_owned(),
        };
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                &decision_reader.provider,
                &decision_reader.subject,
                READ_AUTHORIZATION_DECISION,
                AUTHORIZATION_DECISION_RESOURCE
            ],
        )
        .unwrap();
        assert!(authorize(
            &conn,
            &decision_reader,
            "maker-main",
            READ_AUTHORIZATION_DECISION,
            Some(AUTHORIZATION_DECISION_RESOURCE),
        )
        .unwrap());
        for (capability, resource) in [
            (
                READ_AUTHORIZATION_POLICY,
                Some(AUTHORIZATION_POLICY_RESOURCE),
            ),
            (
                READ_AUTHORIZATION_POLICY_INTEGRITY,
                Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            ),
            (READ_EXECUTION_AUDIT, None),
            (
                READ_EXECUTION_AUDIT_SWEEP,
                Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            ),
            (MANAGE_CHANNELS, None),
        ] {
            assert!(
                !authorize(&conn, &decision_reader, "maker-main", capability, resource).unwrap(),
                "decision authority leaked into {capability}"
            );
        }

        let other_reader = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "other-reader".to_owned(),
        };
        for (capability, resource) in [
            (
                READ_AUTHORIZATION_POLICY,
                Some(AUTHORIZATION_POLICY_RESOURCE),
            ),
            (
                READ_AUTHORIZATION_POLICY_INTEGRITY,
                Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            ),
            (READ_EXECUTION_AUDIT, None),
            (
                READ_EXECUTION_AUDIT_SWEEP,
                Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            ),
            (MANAGE_CHANNELS, None),
        ] {
            conn.execute(
                "INSERT INTO principal_grants
                    (principal_provider, principal_subject, participant_id, capability, resource)
                 VALUES (?1, ?2, 'maker-main', ?3, ?4)",
                params![
                    &other_reader.provider,
                    &other_reader.subject,
                    capability,
                    resource
                ],
            )
            .unwrap();
        }
        assert!(!authorize(
            &conn,
            &other_reader,
            "maker-main",
            READ_AUTHORIZATION_DECISION,
            Some(AUTHORIZATION_DECISION_RESOURCE),
        )
        .unwrap());
    }

    #[test]
    fn authorization_policy_snapshot_reads_full_lifecycle_without_filtering() {
        let (_dir, conn) = setup();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource, status)
             VALUES ('oidc:https://issuer.example', 'active-agent', 'maker-main', 'post_message', 'alpha', 'active')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource, status)
             VALUES ('oidc:https://issuer.example', 'inactive-agent', 'maker-main', 'read_messages', NULL, 'inactive')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, expires_at, one_shot, status)
             VALUES ('oidc:https://issuer.example', 'expired-agent', 'maker-main', 'post_message',
                     'beta', 'expired-intent', unixepoch() - 60, 0, 'active')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, one_shot, consumed_at, consumed_intent_id, status)
             VALUES ('oidc:https://issuer.example', 'consumed-agent', 'maker-main', 'post_message',
                     'gamma', 'consumed-intent', 1, unixepoch(), 'consumed-intent', 'inactive')",
            [],
        )
        .unwrap();

        let snapshot = read_authorization_policy_snapshot(&conn).unwrap();
        assert_eq!(snapshot.durable_grants.len(), 2);
        assert_eq!(snapshot.delegated_grants.len(), 2);
        assert_eq!(snapshot.durable_grants[0].status, "active");
        assert_eq!(snapshot.durable_grants[1].status, "inactive");
        assert_eq!(
            snapshot.delegated_grants[0].intent_id.as_deref(),
            Some("expired-intent")
        );
        assert!(snapshot.delegated_grants[0].expires_at.is_some());
        assert!(snapshot.delegated_grants[1].one_shot);
        assert!(snapshot.delegated_grants[1].consumed_at.is_some());
        assert_eq!(
            snapshot.delegated_grants[1].consumed_intent_id.as_deref(),
            Some("consumed-intent")
        );
        assert_eq!(snapshot.delegated_grants[1].status, "inactive");
    }

    #[test]
    fn authorization_policy_snapshot_refuses_legacy_schema_without_mutation() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE principal_grants (
                id INTEGER PRIMARY KEY,
                principal_provider TEXT NOT NULL,
                principal_subject TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                resource TEXT,
                status TEXT NOT NULL
            );
            CREATE TABLE delegated_grants (
                id INTEGER PRIMARY KEY,
                principal_provider TEXT NOT NULL,
                principal_subject TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                resource TEXT,
                intent_id TEXT,
                expires_at INTEGER,
                one_shot INTEGER NOT NULL,
                consumed_at INTEGER,
                consumed_intent_id TEXT,
                status TEXT NOT NULL
            );",
        )
        .unwrap();

        assert!(!authorization_policy_snapshot_schema_current(&conn).unwrap());
        assert!(read_authorization_policy_snapshot(&conn).is_err());
        let durable_has_created_at =
            table_has_columns(&conn, "principal_grants", &["created_at"]).unwrap();
        assert!(!durable_has_created_at);
    }

    #[test]
    fn authorization_policy_snapshot_implicit_authority_is_human_admin_only() {
        let (_dir, conn) = setup();
        let human = Principal {
            provider: "human-web".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let hmac = Principal {
            provider: "participant-hmac".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let github = Principal {
            provider: "github".to_owned(),
            subject: "543608".to_owned(),
        };
        let oidc = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "agent-1".to_owned(),
        };

        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_RESOURCE),
        )
        .unwrap());
        identity::set_web_participant_role(&conn, "maker-main", "admin").unwrap();
        assert!(authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_RESOURCE),
        )
        .unwrap());
        for principal in [&hmac, &github, &oidc] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                READ_AUTHORIZATION_POLICY,
                Some(AUTHORIZATION_POLICY_RESOURCE),
            )
            .unwrap());
        }
    }

    #[test]
    fn authorization_policy_snapshot_authority_does_not_imply_other_privileged_capabilities() {
        let (_dir, conn) = setup();
        let oidc = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "policy-reader".to_owned(),
        };
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                oidc.provider,
                oidc.subject,
                READ_AUTHORIZATION_POLICY,
                AUTHORIZATION_POLICY_RESOURCE
            ],
        )
        .unwrap();

        assert!(authorize(
            &conn,
            &oidc,
            "maker-main",
            READ_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_RESOURCE),
        )
        .unwrap());

        for (capability, resource) in [
            (
                READ_AUTHORIZATION_POLICY_INTEGRITY,
                Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            ),
            (READ_EXECUTION_AUDIT, None),
            (
                READ_EXECUTION_AUDIT_SWEEP,
                Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            ),
            (MANAGE_CHANNELS, None),
        ] {
            assert!(
                !authorize(&conn, &oidc, "maker-main", capability, resource).unwrap(),
                "snapshot authority leaked into {capability}"
            );
        }
    }

    #[test]
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
        let (_dir, conn) = setup();
        let principal = Principal {
            provider: "github".into(),
            subject: "543608".into(),
        };
        let decision = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            None,
        )
        .unwrap();
        assert!(decision.allowed);
        assert_eq!(decision.reason, "implicit_github_owner");
        assert!(authorize(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems")
        )
        .unwrap());
    }

    #[test]
    fn explicit_resource_grant_restricts_legacy_owner_fallback() {
        let (_dir, conn) = setup();
        ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('github', '543608', 'maker-main', 'post_message', 'blackboard-lounge')",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "github".into(),
            subject: "543608".into(),
        };
        assert!(authorize(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("blackboard-lounge")
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems")
        )
        .unwrap());
        let denied = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            None,
        )
        .unwrap();
        assert_eq!(denied.reason, "explicit_resource_scope_mismatch");

        let grants = effective_grants(&conn, &principal, "maker-main").unwrap();
        assert!(grants.iter().any(|grant| {
            grant.capability == POST_MESSAGE
                && grant.resource.as_deref() == Some("blackboard-lounge")
                && grant.origin == "explicit"
        }));
        assert!(grants.iter().any(|grant| {
            grant.capability == READ_MESSAGES
                && grant.resource.is_none()
                && grant.origin == "implicit"
        }));
    }

    #[test]
    fn delegated_grant_enforces_resource_intent_and_expiry() {
        let (_dir, conn) = setup();
        ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource, intent_id, expires_at, one_shot)
             VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', 'control-systems', 'intent-1', unixepoch() + 3600, 1)",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "oidc:https://issuer.example".into(),
            subject: "agent-1".into(),
        };
        let matched = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            Some("intent-1"),
        )
        .unwrap();
        assert!(matched.allowed);
        assert_eq!(matched.reason, "delegated_grant_match");
        assert!(matched.consume_grant_id.is_some());

        let wrong_resource = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("blackboard-lounge"),
            Some("intent-1"),
        )
        .unwrap();
        assert_eq!(wrong_resource.reason, "delegated_resource_mismatch");

        let wrong_intent = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            Some("intent-2"),
        )
        .unwrap();
        assert_eq!(wrong_intent.reason, "delegated_intent_mismatch");

        conn.execute(
            "UPDATE delegated_grants SET expires_at = unixepoch() - 1 WHERE principal_subject = 'agent-1'",
            [],
        )
        .unwrap();
        let expired = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            Some("intent-1"),
        )
        .unwrap();
        assert_eq!(expired.reason, "delegated_grant_expired");
    }

    #[test]
    fn consumed_one_shot_distinguishes_replay_from_broken_consumption() {
        let (_dir, conn) = setup();
        ensure_grant_schema(&conn).unwrap();
        execution::migrate_execution_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource, intent_id, one_shot, consumed_at, consumed_intent_id)
             VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', 'control-systems', 'intent-replay', 1, unixepoch(), 'intent-replay')",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "oidc:https://issuer.example".into(),
            subject: "agent-1".into(),
        };
        let broken = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            Some("intent-replay"),
        )
        .unwrap();
        assert!(!broken.allowed);
        assert_eq!(
            broken.reason,
            "delegated_consumed_without_committed_receipt"
        );

        conn.execute(
            "INSERT INTO channels (name, visibility, status, created_by)
             VALUES ('control-systems', 'private', 'active', 'maker-main')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO messages (channel, source, instance, kind, body)
             VALUES ('control-systems', 'maker', 'maker-main', 'message', 'replay')",
            [],
        )
        .unwrap();
        let message_id = conn.last_insert_rowid();
        conn.execute(
            "INSERT INTO execution_receipts
                (participant_id, intent_id, intent_hash, capability, message_id, status)
             VALUES ('maker-main', 'intent-replay', 'hash', 'post_message', ?1, 'committed')",
            [message_id],
        )
        .unwrap();
        let replay = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            Some("intent-replay"),
        )
        .unwrap();
        assert!(replay.allowed);
        assert_eq!(replay.reason, "delegated_committed_replay");
        assert!(replay.consume_grant_id.is_none());
    }

    #[test]
    fn inactive_participant_denies_all_grants() {
        let (_dir, conn) = setup();
        identity::set_web_participant_status(&conn, "maker-main", "inactive").unwrap();
        let principal = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        let decision = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("blackboard-lounge"),
            None,
        )
        .unwrap();
        assert_eq!(decision.reason, "participant_inactive");
        assert!(!authorize(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("blackboard-lounge")
        )
        .unwrap());
        assert!(effective_grants(&conn, &principal, "maker-main")
            .unwrap()
            .is_empty());
    }

    #[test]
    fn explicit_execution_audit_scope_suppresses_implicit_fallback() {
        let (_dir, conn) = setup();
        ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('participant-hmac', 'maker-main', 'maker-main', 'read_execution_audit', 'intent-allowed')",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        assert!(authorize(
            &conn,
            &principal,
            "maker-main",
            READ_EXECUTION_AUDIT,
            Some("intent-allowed"),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &principal,
            "maker-main",
            READ_EXECUTION_AUDIT,
            Some("intent-denied"),
        )
        .unwrap());
    }

    #[test]
    fn audit_sweep_implicit_authority_is_human_web_admin_only() {
        let (_dir, conn) = setup();
        let human = Principal {
            provider: "human-web".into(),
            subject: "maker-main".into(),
        };
        let hmac = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        let github = Principal {
            provider: "github".into(),
            subject: "543608".into(),
        };

        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &hmac,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &github,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
        )
        .unwrap());

        conn.execute(
            "UPDATE web_participants SET role = 'admin' WHERE participant_id = 'maker-main'",
            [],
        )
        .unwrap();
        let decision = evaluate_authorization(
            &conn,
            &human,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            None,
        )
        .unwrap();
        assert!(decision.allowed);
        assert_eq!(decision.reason, "implicit_human_web_admin_audit_sweep");
        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some("wrong-resource"),
        )
        .unwrap());

        let grants = effective_grants(&conn, &human, "maker-main").unwrap();
        assert!(grants.iter().any(|grant| {
            grant.capability == READ_EXECUTION_AUDIT_SWEEP
                && grant.resource.as_deref() == Some(EXECUTION_AUDIT_SWEEP_RESOURCE)
                && grant.origin == "implicit"
        }));
    }

    #[test]
    fn policy_integrity_implicit_authority_is_human_web_admin_only() {
        let (_dir, conn) = setup();
        let human = Principal {
            provider: "human-web".into(),
            subject: "maker-main".into(),
        };
        let hmac = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        let github = Principal {
            provider: "github".into(),
            subject: "543608".into(),
        };

        for principal in [&human, &hmac, &github] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                READ_AUTHORIZATION_POLICY_INTEGRITY,
                Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            )
            .unwrap());
        }

        conn.execute(
            "UPDATE web_participants SET role = 'admin' WHERE participant_id = 'maker-main'",
            [],
        )
        .unwrap();
        let decision = evaluate_authorization(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            None,
        )
        .unwrap();
        assert!(decision.allowed);
        assert_eq!(
            decision.reason,
            "implicit_human_web_admin_authorization_policy_integrity"
        );
        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some("wrong-resource"),
        )
        .unwrap());

        let grants = effective_grants(&conn, &human, "maker-main").unwrap();
        assert!(grants.iter().any(|grant| {
            grant.capability == READ_AUTHORIZATION_POLICY_INTEGRITY
                && grant.resource.as_deref() == Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE)
                && grant.origin == "implicit"
        }));
    }

    #[test]
    fn explicit_grant_can_authorize_participant_hmac_policy_integrity() {
        let (_dir, conn) = setup();
        ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('participant-hmac', 'maker-main', 'maker-main',
                     'read_authorization_policy_integrity', 'authorization-policy-integrity')",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        let decision = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            None,
        )
        .unwrap();
        assert!(decision.allowed);
        assert_eq!(decision.reason, "explicit_durable_grant_match");
    }

    #[test]
    fn explicit_grant_can_authorize_participant_hmac_audit_sweep() {
        let (_dir, conn) = setup();
        ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('participant-hmac', 'maker-main', 'maker-main',
                     'read_execution_audit_sweep', 'execution-audit-sweep')",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        let decision = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            None,
        )
        .unwrap();
        assert!(decision.allowed);
        assert_eq!(decision.reason, "explicit_durable_grant_match");
    }
}
