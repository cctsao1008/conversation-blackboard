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

pub fn ensure_execution_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS ingress_provenance (
            delivery_id         TEXT PRIMARY KEY,
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
            ON execution_receipts(message_id);",
    )
}

pub fn get_execution_receipt(
    conn: &Connection,
    participant_id: &str,
    intent_id: &str,
) -> rusqlite::Result<Option<ExecutionReceipt>> {
    ensure_execution_tables(conn)?;
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

pub fn execute_message_intent(
    conn: &Connection,
    identity: &Identity,
    request: MessageExecutionRequest<'_>,
) -> rusqlite::Result<MessageExecutionResult> {
    ensure_execution_tables(conn)?;

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

    if !authorization::authorize(
        conn,
        &request.authority.principal,
        &request.intent.participant_id,
        &request.intent.capability,
        Some(&request.intent.resource),
    )? {
        return Ok(MessageExecutionResult::AuthorizationDenied);
    }

    let tx = conn.unchecked_transaction()?;
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
    ensure_execution_tables(conn)?;
    let tx = conn.unchecked_transaction()?;
    record_execution_in_tx(&tx, ingress, receipt)?;
    tx.commit()
}

fn record_execution_in_tx(
    tx: &Transaction<'_>,
    ingress: &IngressProvenance,
    receipt: &ExecutionReceipt,
) -> rusqlite::Result<()> {
    let existing_delivery: Option<(String, String, String, String, String)> = tx
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
        if existing != expected {
            return Err(rusqlite::Error::InvalidQuery);
        }
    } else {
        tx.execute(
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
        ensure_execution_tables(&conn).unwrap();

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
    }

    #[test]
    fn audit_failure_rolls_back_message_navigation_and_receipt() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        provision_test_participant(&conn);
        ensure_execution_tables(&conn).unwrap();

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
    }
}
