use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;

use crate::execution::Principal;

pub const READ_MESSAGES: &str = "read_messages";
pub const POST_MESSAGE: &str = "post_message";
pub const REPLY: &str = "reply";
pub const READ_EXECUTION_RECEIPT: &str = "read_execution_receipt";
pub const MANAGE_CHANNELS: &str = "manage_channels";

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct EffectiveGrant {
    pub capability: String,
    pub resource: Option<String>,
    pub origin: String,
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
    if !explicit.is_empty() {
        return Ok(explicit);
    }

    let mut grants = Vec::new();
    for capability in [
        READ_MESSAGES,
        POST_MESSAGE,
        REPLY,
        READ_EXECUTION_RECEIPT,
        MANAGE_CHANNELS,
    ] {
        if implicit_authority(principal, participant_id, capability, &participant) {
            grants.push(EffectiveGrant {
                capability: capability.to_owned(),
                resource: None,
                origin: "implicit".to_owned(),
            });
        }
    }
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
    }
}
