use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde::Serialize;

use crate::execution::Principal;

pub const READ_MESSAGES: &str = "read_messages";
pub const POST_MESSAGE: &str = "post_message";
pub const REPLY: &str = "reply";
pub const READ_EXECUTION_RECEIPT: &str = "read_execution_receipt";
pub const MANAGE_CHANNELS: &str = "manage_channels";

const KNOWN_CAPABILITIES: [&str; 5] = [
    READ_MESSAGES,
    POST_MESSAGE,
    REPLY,
    READ_EXECUTION_RECEIPT,
    MANAGE_CHANNELS,
];

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct EffectiveGrant {
    pub capability: String,
    pub resource: Option<String>,
    pub origin: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IntentAuthorization {
    Denied,
    Allowed { consume_grant_id: Option<i64> },
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

pub fn authorize(
    conn: &Connection,
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
) -> rusqlite::Result<bool> {
    ensure_grant_schema(conn)?;
    let Some(participant) = participant_policy_row(conn, participant_id)? else {
        return Ok(false);
    };
    if participant.status != "active" {
        return Ok(false);
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
        let matched: i64 = conn.query_row(
            "SELECT COUNT(*)
             FROM principal_grants
             WHERE principal_provider = ?1
               AND principal_subject = ?2
               AND participant_id = ?3
               AND capability = ?4
               AND status = 'active'
               AND (resource IS NULL OR resource = ?5)",
            params![
                principal.provider,
                principal.subject,
                participant_id,
                capability,
                resource
            ],
            |row| row.get(0),
        )?;
        return Ok(matched > 0);
    }

    Ok(implicit_authority(
        principal,
        participant_id,
        capability,
        &participant,
    ))
}

pub fn authorize_for_intent(
    conn: &Connection,
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: &str,
) -> rusqlite::Result<IntentAuthorization> {
    ensure_grant_schema(conn)?;
    let Some(participant) = participant_policy_row(conn, participant_id)? else {
        return Ok(IntentAuthorization::Denied);
    };
    if participant.status != "active" {
        return Ok(IntentAuthorization::Denied);
    }

    if authorize(conn, principal, participant_id, capability, resource)? {
        return Ok(IntentAuthorization::Allowed {
            consume_grant_id: None,
        });
    }

    let delegated = conn
        .query_row(
            "SELECT id, one_shot, consumed_at, consumed_intent_id
             FROM delegated_grants
             WHERE principal_provider = ?1
               AND principal_subject = ?2
               AND participant_id = ?3
               AND capability = ?4
               AND status = 'active'
               AND (resource IS NULL OR resource = ?5)
               AND (intent_id IS NULL OR intent_id = ?6)
               AND (
                    expires_at IS NULL
                    OR expires_at > unixepoch()
                    OR (one_shot = 1 AND consumed_intent_id = ?6)
               )
               AND (
                    one_shot = 0
                    OR consumed_at IS NULL
                    OR consumed_intent_id = ?6
               )
             ORDER BY
               (intent_id IS NOT NULL) DESC,
               (resource IS NOT NULL) DESC,
               one_shot DESC,
               id ASC
             LIMIT 1",
            params![
                principal.provider,
                principal.subject,
                participant_id,
                capability,
                resource,
                intent_id
            ],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, i64>(1)? != 0,
                    row.get::<_, Option<i64>>(2)?,
                    row.get::<_, Option<String>>(3)?,
                ))
            },
        )
        .optional()?;

    let Some((grant_id, one_shot, consumed_at, consumed_intent_id)) = delegated else {
        return Ok(IntentAuthorization::Denied);
    };

    if one_shot && consumed_at.is_some() {
        if consumed_intent_id.as_deref() != Some(intent_id) {
            return Ok(IntentAuthorization::Denied);
        }
        let committed: i64 = conn.query_row(
            "SELECT COUNT(*) FROM execution_receipts
             WHERE participant_id = ?1 AND intent_id = ?2 AND status = 'committed'",
            params![participant_id, intent_id],
            |row| row.get(0),
        )?;
        return Ok(if committed > 0 {
            IntentAuthorization::Allowed {
                consume_grant_id: None,
            }
        } else {
            IntentAuthorization::Denied
        });
    }

    Ok(IntentAuthorization::Allowed {
        consume_grant_id: one_shot.then_some(grant_id),
    })
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
        if authorize(conn, principal, participant_id, capability, None)? {
            grants.push(EffectiveGrant {
                capability: capability.to_owned(),
                resource: None,
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

fn implicit_authority(
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    participant: &ParticipantPolicyRow,
) -> bool {
    let self_authenticated = matches!(
        principal.provider.as_str(),
        "participant-hmac" | "human-web"
    ) && principal.subject == participant_id;
    if self_authenticated {
        if capability == MANAGE_CHANNELS {
            return principal.provider == "human-web" && participant.role == "admin";
        }
        return matches!(
            capability,
            READ_MESSAGES | POST_MESSAGE | REPLY | READ_EXECUTION_RECEIPT
        );
    }

    let github_owner = principal.provider == "github"
        && participant.owner_provider.as_deref() == Some("github")
        && participant.owner_subject.as_deref() == Some(principal.subject.as_str());
    github_owner
        && matches!(
            capability,
            READ_MESSAGES | POST_MESSAGE | REPLY | READ_EXECUTION_RECEIPT
        )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{db, identity};
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
        assert!(matches!(
            authorize_for_intent(
                &conn,
                &principal,
                "maker-main",
                POST_MESSAGE,
                Some("control-systems"),
                "intent-1"
            )
            .unwrap(),
            IntentAuthorization::Allowed {
                consume_grant_id: Some(_)
            }
        ));
        assert_eq!(
            authorize_for_intent(
                &conn,
                &principal,
                "maker-main",
                POST_MESSAGE,
                Some("blackboard-lounge"),
                "intent-1"
            )
            .unwrap(),
            IntentAuthorization::Denied
        );
        assert_eq!(
            authorize_for_intent(
                &conn,
                &principal,
                "maker-main",
                POST_MESSAGE,
                Some("control-systems"),
                "intent-2"
            )
            .unwrap(),
            IntentAuthorization::Denied
        );
        conn.execute(
            "UPDATE delegated_grants SET expires_at = unixepoch() - 1 WHERE principal_subject = 'agent-1'",
            [],
        )
        .unwrap();
        assert_eq!(
            authorize_for_intent(
                &conn,
                &principal,
                "maker-main",
                POST_MESSAGE,
                Some("control-systems"),
                "intent-1"
            )
            .unwrap(),
            IntentAuthorization::Denied
        );
    }

    #[test]
    fn inactive_participant_denies_all_grants() {
        let (_dir, conn) = setup();
        identity::set_web_participant_status(&conn, "maker-main", "inactive").unwrap();
        let principal = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
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
}
