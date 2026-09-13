from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")

# authorization.rs
path = "src/authorization.rs"
replace_once(
    path,
    "use rusqlite::{params, Connection, OptionalExtension};",
    "use rusqlite::{params, Connection, OptionalExtension, Transaction};",
)
replace_once(
    path,
    "#[derive(Debug, Clone)]\nstruct ParticipantPolicyRow",
    "#[derive(Debug, Clone, PartialEq, Eq)]\npub enum IntentAuthorization {\n    Denied,\n    Allowed { consume_grant_id: Option<i64> },\n}\n\n#[derive(Debug, Clone)]\nstruct ParticipantPolicyRow",
)
replace_once(
    path,
    "        CREATE INDEX IF NOT EXISTS idx_principal_grants_lookup\n            ON principal_grants (\n                principal_provider,\n                principal_subject,\n                participant_id,\n                capability,\n                status\n            );",
    "        CREATE INDEX IF NOT EXISTS idx_principal_grants_lookup\n            ON principal_grants (\n                principal_provider,\n                principal_subject,\n                participant_id,\n                capability,\n                status\n            );\n\n        CREATE TABLE IF NOT EXISTS delegated_grants (\n            id                  INTEGER PRIMARY KEY AUTOINCREMENT,\n            principal_provider  TEXT NOT NULL,\n            principal_subject   TEXT NOT NULL,\n            participant_id      TEXT NOT NULL,\n            capability          TEXT NOT NULL,\n            resource            TEXT,\n            intent_id           TEXT,\n            expires_at          INTEGER,\n            one_shot            INTEGER NOT NULL DEFAULT 0 CHECK (one_shot IN (0, 1)),\n            consumed_at         INTEGER,\n            consumed_intent_id  TEXT,\n            status              TEXT NOT NULL DEFAULT 'active'\n                                CHECK (status IN ('active', 'inactive')),\n            created_at          INTEGER NOT NULL DEFAULT (unixepoch()),\n            updated_at          INTEGER NOT NULL DEFAULT (unixepoch())\n        );\n        CREATE INDEX IF NOT EXISTS idx_delegated_grants_lookup\n            ON delegated_grants (\n                principal_provider,\n                principal_subject,\n                participant_id,\n                capability,\n                status\n            );",
)
marker = "pub fn effective_grants(\n"
insert = r'''pub fn authorize_for_intent(
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

'''
replace_once(path, marker, insert + marker)

# Add authorization tests.
replace_once(
    path,
    "    #[test]\n    fn inactive_participant_denies_all_grants()",
    r'''    #[test]
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
    fn inactive_participant_denies_all_grants()''',
)

# execution.rs: move authorization inside transaction and consume one-shot grants atomically.
path = "src/execution.rs"
replace_once(
    path,
    '''    if !authorization::authorize(
        conn,
        &request.authority.principal,
        &request.intent.participant_id,
        &request.intent.capability,
        Some(&request.intent.resource),
    )? {
        return Ok(MessageExecutionResult::AuthorizationDenied);
    }

    let tx = conn.unchecked_transaction()?;
''',
    '''    let tx = conn.unchecked_transaction()?;
    let consume_grant_id = match authorization::authorize_for_intent(
        &tx,
        &request.authority.principal,
        &request.intent.participant_id,
        &request.intent.capability,
        Some(&request.intent.resource),
        &request.intent.intent_id,
    )? {
        authorization::IntentAuthorization::Denied => {
            return Ok(MessageExecutionResult::AuthorizationDenied)
        }
        authorization::IntentAuthorization::Allowed { consume_grant_id } => consume_grant_id,
    };
''',
)
replace_once(
    path,
    "    record_execution_in_tx(&tx, request.ingress, &receipt)?;\n    tx.commit()?;",
    "    record_execution_in_tx(&tx, request.ingress, &receipt)?;\n    if created {\n        if let Some(grant_id) = consume_grant_id {\n            authorization::consume_delegated_grant_in_tx(\n                &tx,\n                grant_id,\n                &request.intent.intent_id,\n            )?;\n        }\n    }\n    tx.commit()?;",
)

# Add end-to-end atomic-consumption regression test.
replace_once(
    path,
    "    #[test]\n    fn audit_failure_rolls_back_message_navigation_and_receipt()",
    r'''    #[test]
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
            request_hash: message_request_hash("blackboard-lounge", "message", "delegated", None, None),
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
            request_hash: message_request_hash("blackboard-lounge", "message", "second", None, None),
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
    }

    #[test]
    fn audit_failure_rolls_back_message_navigation_and_receipt()''',
)
