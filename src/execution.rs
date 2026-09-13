use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

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
    if value.is_empty()
        || value.len() > MAX_INTENT_ID_BYTES
        || value.chars().any(char::is_control)
    {
        return Err(());
    }
    Ok(value.to_owned())
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

pub fn record_execution(
    conn: &Connection,
    ingress: &IngressProvenance,
    receipt: &ExecutionReceipt,
) -> rusqlite::Result<()> {
    ensure_execution_tables(conn)?;

    let tx = conn.unchecked_transaction()?;

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
        let expected = (
            receipt.intent_hash.clone(),
            receipt.capability.clone(),
            receipt.message_id,
            receipt.status.clone(),
        );
        if existing != expected {
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

    tx.commit()
}

#[cfg(test)]
mod tests {
    use super::*;

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
            .query_row("SELECT COUNT(*) FROM execution_receipts", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 1);

        let mut changed = receipt.clone();
        changed.message_id = 59;
        assert!(record_execution(&conn, &ingress, &changed).is_err());
    }
}
