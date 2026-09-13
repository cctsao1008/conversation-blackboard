from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"anchor not found in {path}: {old[:180]!r}")
    p.write_text(text.replace(old, new, 1))


# Preserve retries across request-hash evolution by checking persisted semantics.
replace_once(
    "src/db.rs",
    '''        if stored_hash != input.request_hash {
            return Ok(NavigationAppendResult::NonceConflict);
        }
        let message = message_by_id(&tx, message_id)?;
        return Ok(NavigationAppendResult::Existing(message));''',
    '''        let message = message_by_id(&tx, message_id)?;
        if stored_hash != input.request_hash
            && (message.channel != input.channel
                || message.kind != input.kind
                || message.body != input.body
                || message.reply_to != input.reply_to
                || message.conversation_ref.as_deref() != conversation_ref)
        {
            return Ok(NavigationAppendResult::NonceConflict);
        }
        return Ok(NavigationAppendResult::Existing(message));''',
)

# REST signed writes: nonce is delivery identity; optional intent_id is semantic identity.
replace_once(
    "src/http.rs",
    '''            "reply_to",
            "nonce",
            "auth",''',
    '''            "reply_to",
            "nonce",
            "intent_id",
            "auth",''',
)
replace_once(
    "src/http.rs",
    '''    let auth = body
        .get("auth")''',
    '''    let explicit_intent_id = match body.get("intent_id") {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) => Some(
            execution::normalize_intent_id(value)
                .map_err(|_| ApiError::new(StatusCode::BAD_REQUEST, "invalid_intent_id"))?,
        ),
        _ => return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_intent_id")),
    };
    let semantic_intent_id = explicit_intent_id
        .clone()
        .unwrap_or_else(|| nonce.clone());
    let auth = body
        .get("auth")''',
)
replace_once(
    "src/http.rs",
    '''    let verify_nonce = nonce.clone();
    let writer = with_db(state, move |conn| {''',
    '''    let verify_nonce = nonce.clone();
    let verify_intent_id = explicit_intent_id.clone();
    let writer = with_db(state, move |conn| {''',
)
replace_once(
    "src/http.rs",
    '''            || !participant_auth::verify_write_proof(
                &record.auth_secret,
                &proof,
                &lookup_participant,
                &verify_channel,
                &verify_kind,
                &verify_body,
                reply_to,
                &verify_nonce,
            )''',
    '''            || !participant_auth::verify_write_proof_with_intent(
                &record.auth_secret,
                &proof,
                &lookup_participant,
                &verify_channel,
                &verify_kind,
                &verify_body,
                reply_to,
                &verify_nonce,
                verify_intent_id.as_deref(),
            )''',
)
replace_once(
    "src/http.rs",
    '''        intent_id: nonce.clone(),
        participant_id: participant_id.clone(),''',
    '''        intent_id: semantic_intent_id.clone(),
        participant_id: participant_id.clone(),''',
)
replace_once(
    "src/http.rs",
    '''        intent_id: nonce.clone(),
        transport: "rest".to_owned(),''',
    '''        intent_id: semantic_intent_id.clone(),
        transport: "rest".to_owned(),''',
)
replace_once(
    "src/http.rs",
    '''            "status": status,
            "idempotent": idempotent,
            "message": persisted,''',
    '''            "status": status,
            "idempotent": idempotent,
            "intent_id": semantic_intent_id,
            "delivery_id": nonce,
            "message": persisted,''',
)

# MCP write schema and implementation mirror the same semantic/delivery split.
replace_once(
    "src/mcp.rs",
    '''                        "reply_to": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
                        "nonce": {"type": "string", "minLength": 1, "maxLength": 128}''',
    '''                        "reply_to": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
                        "nonce": {"type": "string", "minLength": 1, "maxLength": 128},
                        "intent_id": {"type": "string", "minLength": 1, "maxLength": 256}''',
)
replace_once(
    "src/mcp.rs",
    '''            "reply_to": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
        },
        "required": ["status", "idempotent", "id", "source", "participant_id", "instance", "channel", "kind", "reply_to"],''',
    '''            "reply_to": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "intent_id": {"type": "string"},
            "delivery_id": {"type": "string"}
        },
        "required": ["status", "idempotent", "id", "source", "participant_id", "instance", "channel", "kind", "reply_to", "intent_id", "delivery_id"],''',
)
replace_once(
    "src/mcp.rs",
    '''            "reply_to",
            "nonce",
        ],''',
    '''            "reply_to",
            "nonce",
            "intent_id",
        ],''',
)
replace_once(
    "src/mcp.rs",
    '''    let writer = match resolve_write_identity(
        state,
        arguments,
        &participant_id,
        &channel,
        &kind,
        &message_body,
        reply_to,
        &nonce,
    )''',
    '''    let explicit_intent_id = match arguments.get("intent_id") {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) => match execution::normalize_intent_id(value) {
            Ok(value) => Some(value),
            Err(()) => return tool_error("invalid_intent_id"),
        },
        _ => return tool_error("invalid_intent_id"),
    };
    let semantic_intent_id = explicit_intent_id
        .clone()
        .unwrap_or_else(|| nonce.clone());

    let writer = match resolve_write_identity(
        state,
        arguments,
        &participant_id,
        &channel,
        &kind,
        &message_body,
        reply_to,
        &nonce,
        explicit_intent_id.as_deref(),
    )''',
)
replace_once(
    "src/mcp.rs",
    '''        intent_id: nonce.clone(),
        participant_id: participant_id.clone(),''',
    '''        intent_id: semantic_intent_id.clone(),
        participant_id: participant_id.clone(),''',
)
replace_once(
    "src/mcp.rs",
    '''        intent_id: nonce.clone(),
        transport: "mcp".to_owned(),''',
    '''        intent_id: semantic_intent_id.clone(),
        transport: "mcp".to_owned(),''',
)
replace_once(
    "src/mcp.rs",
    '''        "kind": persisted.kind,
        "reply_to": persisted.reply_to
    }))''',
    '''        "kind": persisted.kind,
        "reply_to": persisted.reply_to,
        "intent_id": semantic_intent_id,
        "delivery_id": nonce
    }))''',
)
replace_once(
    "src/mcp.rs",
    '''    nonce: &str,
) -> Result<Identity, &'static str> {''',
    '''    nonce: &str,
    intent_id: Option<&str>,
) -> Result<Identity, &'static str> {''',
)
replace_once(
    "src/mcp.rs",
    '''    if !participant_auth::verify_write_proof(
        &auth.auth_secret,
        &proof,
        participant_id,
        channel,
        kind,
        body,
        reply_to,
        nonce,
    ) {''',
    '''    if !participant_auth::verify_write_proof_with_intent(
        &auth.auth_secret,
        &proof,
        participant_id,
        channel,
        kind,
        body,
        reply_to,
        nonce,
        intent_id,
    ) {''',
)

# Regression test: old request-hash retries remain idempotent when semantics match.
insert_anchor = '''    #[test]\n    fn migration_preserves_historical_messages_with_null_conversation_ref() {'''
insert_test = '''    #[test]
    fn navigation_retry_tolerates_request_hash_algorithm_evolution_when_semantics_match() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        initialize(&path).unwrap();
        let conn = connect(&path).unwrap();
        let identity = Identity {
            source: "maker".into(),
            instance: "maker-main".into(),
            label: None,
        };

        let first = append_navigation_message(
            &conn,
            &identity,
            NavigationMessageInput {
                channel: "blackboard-lounge",
                kind: "message",
                body: "same semantic request",
                reply_to: None,
                nonce: "legacy-nonce",
                request_hash: "legacy-hash",
            },
        )
        .unwrap();
        let first_id = match first {
            NavigationAppendResult::Created(message) => message.id,
            other => panic!("unexpected result: {other:?}"),
        };

        let retry = append_navigation_message(
            &conn,
            &identity,
            NavigationMessageInput {
                channel: "blackboard-lounge",
                kind: "message",
                body: "same semantic request",
                reply_to: None,
                nonce: "legacy-nonce",
                request_hash: "new-hash",
            },
        )
        .unwrap();
        match retry {
            NavigationAppendResult::Existing(message) => assert_eq!(message.id, first_id),
            other => panic!("unexpected result: {other:?}"),
        }
    }

'''
replace_once("src/db.rs", insert_anchor, insert_test + insert_anchor)
