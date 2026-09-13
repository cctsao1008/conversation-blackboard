from pathlib import Path

p = Path("src/execution.rs")
s = p.read_text(encoding="utf-8")

needle = '''#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExecutionReceipt {
    pub participant_id: String,
    pub intent_id: String,
    pub intent_hash: String,
    pub capability: String,
    pub message_id: i64,
    pub status: String,
}
'''
insert = needle + '''
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AuthorizationProvenance {
    pub participant_id: String,
    pub intent_id: String,
    pub source: String,
    pub reason: String,
    pub grant_id: Option<i64>,
}
'''
if needle not in s:
    raise SystemExit("ExecutionReceipt block not found")
s = s.replace(needle, insert, 1)

needle = '''        CREATE INDEX IF NOT EXISTS idx_execution_receipts_message
            ON execution_receipts(message_id);",
    )
}
'''
insert = '''        CREATE INDEX IF NOT EXISTS idx_execution_receipts_message
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
    )
}
'''
if needle not in s:
    raise SystemExit("execution schema tail not found")
s = s.replace(needle, insert, 1)

needle = '''pub fn execute_message_intent(
'''
getter = '''pub fn get_authorization_provenance(
    conn: &Connection,
    participant_id: &str,
    intent_id: &str,
) -> rusqlite::Result<Option<AuthorizationProvenance>> {
    ensure_execution_tables(conn)?;
    conn.query_row(
        "SELECT participant_id, intent_id, source, reason, grant_id
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
        },
    )
    .optional()
}

pub fn execute_message_intent(
'''
if needle not in s:
    raise SystemExit("execute function marker not found")
s = s.replace(needle, getter, 1)

needle = '''    record_execution_in_tx(&tx, request.ingress, &receipt)?;
    if created {
        if let Some(grant_id) = consume_grant_id {
            authorization::consume_delegated_grant_in_tx(&tx, grant_id, &request.intent.intent_id)?;
        }
    }
'''
insert = '''    record_execution_in_tx(&tx, request.ingress, &receipt)?;
    if created {
        record_authorization_provenance_in_tx(&tx, &receipt, &authorization_decision)?;
        if let Some(grant_id) = consume_grant_id {
            authorization::consume_delegated_grant_in_tx(&tx, grant_id, &request.intent.intent_id)?;
        }
    }
'''
if needle not in s:
    raise SystemExit("record execution block not found")
s = s.replace(needle, insert, 1)

needle = '''fn message_by_id(conn: &Connection, id: i64) -> rusqlite::Result<Message> {
'''
helper = '''fn record_authorization_provenance_in_tx(
    tx: &Transaction<'_>,
    receipt: &ExecutionReceipt,
    decision: &authorization::AuthorizationDecision,
) -> rusqlite::Result<()> {
    if !decision.allowed {
        return Err(rusqlite::Error::InvalidQuery);
    }
    tx.execute(
        "INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, source, reason, grant_id)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        params![
            receipt.participant_id,
            receipt.intent_id,
            decision.source,
            decision.reason,
            decision.grant_id
        ],
    )?;
    Ok(())
}

fn message_by_id(conn: &Connection, id: i64) -> rusqlite::Result<Message> {
'''
if needle not in s:
    raise SystemExit("message_by_id marker not found")
s = s.replace(needle, helper, 1)

needle = '''        assert_eq!(ingress_count, 2);
    }
'''
insert = '''        assert_eq!(ingress_count, 2);
        let provenance = get_authorization_provenance(
            &conn,
            "maker-main",
            "semantic-intent-1",
        )
        .unwrap()
        .unwrap();
        assert_eq!(provenance.source, "implicit_authority");
        assert_eq!(provenance.reason, "implicit_participant_hmac");
        let provenance_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM execution_authorization_provenance
                 WHERE participant_id = 'maker-main' AND intent_id = 'semantic-intent-1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(provenance_count, 1);
    }
'''
if needle not in s:
    raise SystemExit("shared execution test tail not found")
s = s.replace(needle, insert, 1)

needle = '''        assert_eq!(consumed.1.as_deref(), Some("delegated-intent-1"));

        let replay_ingress = IngressProvenance {
'''
insert = '''        assert_eq!(consumed.1.as_deref(), Some("delegated-intent-1"));

        let provenance = get_authorization_provenance(
            &conn,
            "maker-main",
            "delegated-intent-1",
        )
        .unwrap()
        .unwrap();
        assert_eq!(provenance.source, "delegated_grants");
        assert_eq!(provenance.reason, "delegated_grant_match");
        assert!(provenance.grant_id.is_some());
        conn.execute(
            "UPDATE delegated_grants SET status = 'inactive' WHERE principal_subject = 'agent-1'",
            [],
        )
        .unwrap();
        let stable = get_authorization_provenance(
            &conn,
            "maker-main",
            "delegated-intent-1",
        )
        .unwrap()
        .unwrap();
        assert_eq!(stable, provenance);
        conn.execute(
            "UPDATE delegated_grants SET status = 'active' WHERE principal_subject = 'agent-1'",
            [],
        )
        .unwrap();

        let replay_ingress = IngressProvenance {
'''
if needle not in s:
    raise SystemExit("one-shot test insertion point not found")
s = s.replace(needle, insert, 1)

needle = '''        assert!(consumed_at.is_none());
    }
'''
insert = '''        assert!(consumed_at.is_none());
        assert!(get_authorization_provenance(
            &conn,
            "maker-main",
            "delegated-fail-1",
        )
        .unwrap()
        .is_none());
    }
'''
if needle not in s:
    raise SystemExit("failed execution test tail not found")
s = s.replace(needle, insert, 1)

needle = '''        assert_eq!(receipt_count, 0);
    }
}
'''
insert = '''        assert_eq!(receipt_count, 0);
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
'''
if needle not in s:
    raise SystemExit("audit rollback test tail not found")
s = s.replace(needle, insert, 1)

p.write_text(s, encoding="utf-8")
