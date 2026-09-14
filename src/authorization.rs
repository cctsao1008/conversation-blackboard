use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde::Serialize;

use crate::execution::Principal;

pub const READ_MESSAGES: &str = "read_messages";
pub const POST_MESSAGE: &str = "post_message";
pub const REPLY: &str = "reply";
pub const READ_EXECUTION_RECEIPT: &str = "read_execution_receipt";
pub const READ_EXECUTION_AUDIT: &str = "read_execution_audit";
pub const READ_EXECUTION_AUDIT_SWEEP: &str = "read_execution_audit_sweep";
pub const EXECUTION_AUDIT_SWEEP_RESOURCE: &str = "execution-audit-sweep";
pub const MANAGE_CHANNELS: &str = "manage_channels";

const KNOWN_CAPABILITIES: [&str; 7] = [
    READ_MESSAGES,
    POST_MESSAGE,
    REPLY,
    READ_EXECUTION_RECEIPT,
    READ_EXECUTION_AUDIT,
    READ_EXECUTION_AUDIT_SWEEP,
    MANAGE_CHANNELS,
];

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

pub fn evaluate_authorization(
    conn: &Connection,
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: Option<&str>,
) -> rusqlite::Result<AuthorizationDecision> {
    ensure_grant_schema(conn)?;
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
        let implicit_resource =
            (capability == READ_EXECUTION_AUDIT_SWEEP).then_some(EXECUTION_AUDIT_SWEEP_RESOURCE);
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
