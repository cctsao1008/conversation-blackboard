from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"anchor not found in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new, 1))


# GitHub webhook -> shared application execution boundary.
replace_once(
    "src/github_webhook.rs",
    '''    // Preserve the legacy GitHub nonce when intent_id is omitted. An explicit
    // intent_id becomes the semantic idempotency key while delivery_id remains
    // transport-specific.
    let nonce = intent_id.clone();
    let request_hash = identity::hash_token(
        &json!({
            "channel": channel,
            "kind": kind,
            "body": message_body,
            "conversation_ref": conversation_ref,
            "reply_to": reply_to,
        })
        .to_string(),
    );''',
    '''    // Preserve the legacy GitHub semantic key when intent_id is omitted while
    // keeping transport delivery identity independent.
    let request_hash = execution::message_request_hash(
        &channel,
        &kind,
        &message_body,
        conversation_ref.as_deref(),
        reply_to,
    );''',
)
replace_once(
    "src/github_webhook.rs",
    '''            if !db::ensure_channel_for_write(&conn, &channel, Some(&identity.instance))? {
                return Ok(WebhookWriteResult::ChannelArchived);
            }
            let result = match db::append_navigation_message_with_conversation(
                &conn,
                &identity,
                db::NavigationMessageInput {
                    channel: &channel,
                    kind: &kind,
                    body: &message_body,
                    reply_to,
                    nonce: &nonce,
                    request_hash: &request_hash,
                },
                conversation_ref.as_deref(),
            )? {
                db::NavigationAppendResult::Created(message) => {
                    WebhookWriteResult::Created(message.id)
                }
                db::NavigationAppendResult::Existing(message) => {
                    WebhookWriteResult::Existing(message.id)
                }
                db::NavigationAppendResult::NonceConflict => WebhookWriteResult::NonceConflict,
                db::NavigationAppendResult::ReplyTargetNotFound => {
                    WebhookWriteResult::ReplyTargetNotFound
                }
            };

            if let Some(message_id) = match &result {
                WebhookWriteResult::Created(id) | WebhookWriteResult::Existing(id) => Some(*id),
                _ => None,
            } {
                execution::record_execution(
                    &conn,
                    &ingress,
                    &execution::ExecutionReceipt {
                        participant_id: intent_envelope.participant_id.clone(),
                        intent_id: intent_envelope.intent_id.clone(),
                        intent_hash: intent_envelope.request_hash.clone(),
                        capability: intent_envelope.capability.clone(),
                        message_id,
                        status: "committed".to_owned(),
                    },
                )?;
            }

            Ok(result)''',
    '''            Ok(match execution::execute_message_intent(
                &conn,
                &identity,
                &authority_context,
                &ingress,
                &intent_envelope,
                &channel,
                &kind,
                &message_body,
                reply_to,
            )? {
                execution::MessageExecutionResult::Created(message, _) => {
                    WebhookWriteResult::Created(message.id)
                }
                execution::MessageExecutionResult::Existing(message, _) => {
                    WebhookWriteResult::Existing(message.id)
                }
                execution::MessageExecutionResult::IntentConflict => {
                    WebhookWriteResult::NonceConflict
                }
                execution::MessageExecutionResult::ReplyTargetNotFound => {
                    WebhookWriteResult::ReplyTargetNotFound
                }
                execution::MessageExecutionResult::ChannelArchived => {
                    WebhookWriteResult::ChannelArchived
                }
            })''',
)

# REST signed write -> shared application execution boundary.
replace_once(
    "src/http.rs",
    "use crate::{db, identity, model::Identity, participant_auth, request_auth, web_auth};",
    "use crate::{db, execution, identity, model::Identity, participant_auth, request_auth, web_auth};",
)
replace_once(
    "src/http.rs",
    '''    let request_hash = identity::hash_token(
        &json!({
            "channel": &channel,
            "kind": &kind,
            "body": &message_body,
            "reply_to": reply_to,
        })
        .to_string(),
    );
    let write_channel = channel.clone();
    let write_kind = kind.clone();
    let write_body = message_body.clone();
    let write_nonce = nonce.clone();
    let result = with_db(state, move |conn| {
        db::append_navigation_message(
            conn,
            &writer,
            db::NavigationMessageInput {
                channel: &write_channel,
                kind: &write_kind,
                body: &write_body,
                reply_to,
                nonce: &write_nonce,
                request_hash: &request_hash,
            },
        )
    })
    .await?;

    let (status, persisted, idempotent, http_status) = match result {
        db::NavigationAppendResult::Created(message) => {
            ("created", message, false, StatusCode::CREATED)
        }
        db::NavigationAppendResult::Existing(message) => {
            ("existing", message, true, StatusCode::OK)
        }
        db::NavigationAppendResult::NonceConflict => {
            return Err(ApiError::new(StatusCode::CONFLICT, "nonce_conflict"));
        }
        db::NavigationAppendResult::ReplyTargetNotFound => {
            return Err(ApiError::new(
                StatusCode::BAD_REQUEST,
                "reply_target_not_found",
            ));
        }
    };''',
    '''    let request_hash = execution::message_request_hash(
        &channel,
        &kind,
        &message_body,
        None,
        reply_to,
    );
    let principal = execution::Principal {
        provider: "participant-hmac".to_owned(),
        subject: participant_id.clone(),
    };
    let authority = execution::AuthorityContext {
        principal: principal.clone(),
        mechanism: participant_auth::AUTH_SCHEME.to_owned(),
    };
    let intent = execution::IntentEnvelope {
        intent_id: nonce.clone(),
        participant_id: participant_id.clone(),
        conversation_ref: None,
        capability: execution::POST_MESSAGE_CAPABILITY.to_owned(),
        resource: channel.clone(),
        request_hash,
    };
    let ingress = execution::IngressProvenance {
        delivery_id: format!("rest-hmac:{participant_id}:{nonce}"),
        intent_id: nonce.clone(),
        transport: "rest".to_owned(),
        external_ref: nonce.clone(),
        principal,
    };
    let write_channel = channel.clone();
    let write_kind = kind.clone();
    let write_body = message_body.clone();
    let result = with_db(state, move |conn| {
        execution::execute_message_intent(
            conn,
            &writer,
            &authority,
            &ingress,
            &intent,
            &write_channel,
            &write_kind,
            &write_body,
            reply_to,
        )
    })
    .await?;

    let (status, persisted, idempotent, http_status) = match result {
        execution::MessageExecutionResult::Created(message, _) => {
            ("created", message, false, StatusCode::CREATED)
        }
        execution::MessageExecutionResult::Existing(message, _) => {
            ("existing", message, true, StatusCode::OK)
        }
        execution::MessageExecutionResult::IntentConflict => {
            return Err(ApiError::new(StatusCode::CONFLICT, "nonce_conflict"));
        }
        execution::MessageExecutionResult::ReplyTargetNotFound => {
            return Err(ApiError::new(
                StatusCode::BAD_REQUEST,
                "reply_target_not_found",
            ));
        }
        execution::MessageExecutionResult::ChannelArchived => {
            return Err(ApiError::new(StatusCode::CONFLICT, "channel_archived"));
        }
    };''',
)

# MCP HMAC write -> shared application execution boundary.
replace_once(
    "src/mcp.rs",
    "use crate::{db, http::AppState, identity, model::Identity, participant_auth};",
    "use crate::{db, execution, http::AppState, identity, model::Identity, participant_auth};",
)
replace_once(
    "src/mcp.rs",
    '''    let metadata_channel = channel.clone();
    let creator = writer.instance.clone();
    let active = match with_db(state, move |conn| {
        db::ensure_channel_for_write(conn, &metadata_channel, Some(&creator))
    })
    .await
    {
        Ok(value) => value,
        Err(()) => return tool_error("database_unavailable"),
    };
    if !active {
        return tool_error("channel_archived");
    }

    let request_hash = identity::hash_token(
        &json!({
            "channel": &channel,
            "kind": &kind,
            "body": &message_body,
            "reply_to": reply_to,
        })
        .to_string(),
    );

    let write_channel = channel.clone();
    let write_kind = kind.clone();
    let write_body = message_body.clone();
    let write_nonce = nonce.clone();
    let result = match with_db(state, move |conn| {
        db::append_navigation_message(
            conn,
            &writer,
            db::NavigationMessageInput {
                channel: &write_channel,
                kind: &write_kind,
                body: &write_body,
                reply_to,
                nonce: &write_nonce,
                request_hash: &request_hash,
            },
        )
    })
    .await
    {
        Ok(result) => result,
        Err(()) => return tool_error("database_unavailable"),
    };

    let (status, persisted, idempotent) = match result {
        db::NavigationAppendResult::Created(message) => ("created", message, false),
        db::NavigationAppendResult::Existing(message) => ("existing", message, true),
        db::NavigationAppendResult::NonceConflict => return tool_error("nonce_conflict"),
        db::NavigationAppendResult::ReplyTargetNotFound => {
            return tool_error("reply_target_not_found")
        }
    };''',
    '''    let request_hash = execution::message_request_hash(
        &channel,
        &kind,
        &message_body,
        None,
        reply_to,
    );
    let principal = execution::Principal {
        provider: "participant-hmac".to_owned(),
        subject: participant_id.clone(),
    };
    let authority = execution::AuthorityContext {
        principal: principal.clone(),
        mechanism: participant_auth::AUTH_SCHEME.to_owned(),
    };
    let intent = execution::IntentEnvelope {
        intent_id: nonce.clone(),
        participant_id: participant_id.clone(),
        conversation_ref: None,
        capability: execution::POST_MESSAGE_CAPABILITY.to_owned(),
        resource: channel.clone(),
        request_hash,
    };
    let ingress = execution::IngressProvenance {
        delivery_id: format!("mcp-hmac:{participant_id}:{nonce}"),
        intent_id: nonce.clone(),
        transport: "mcp".to_owned(),
        external_ref: nonce.clone(),
        principal,
    };

    let write_channel = channel.clone();
    let write_kind = kind.clone();
    let write_body = message_body.clone();
    let result = match with_db(state, move |conn| {
        execution::execute_message_intent(
            conn,
            &writer,
            &authority,
            &ingress,
            &intent,
            &write_channel,
            &write_kind,
            &write_body,
            reply_to,
        )
    })
    .await
    {
        Ok(result) => result,
        Err(()) => return tool_error("database_unavailable"),
    };

    let (status, persisted, idempotent) = match result {
        execution::MessageExecutionResult::Created(message, _) => ("created", message, false),
        execution::MessageExecutionResult::Existing(message, _) => ("existing", message, true),
        execution::MessageExecutionResult::IntentConflict => return tool_error("nonce_conflict"),
        execution::MessageExecutionResult::ReplyTargetNotFound => {
            return tool_error("reply_target_not_found")
        }
        execution::MessageExecutionResult::ChannelArchived => {
            return tool_error("channel_archived")
        }
    };''',
)
