from pathlib import Path

webhook = Path("src/github_webhook.rs")
text = webhook.read_text()

replacements = []

replacements.append((
    "use crate::{db, identity, model::Identity};",
    "use crate::{db, execution, identity, model::Identity};",
))

replacements.append((
    '''    let conversation_ref = match normalize_conversation_ref(intent.conversation_ref.as_deref()) {
        Ok(value) => value,
        Err(()) => return error_response(StatusCode::BAD_REQUEST, "invalid_blackboard_intent"),
    };

    let participant_id = intent.participant_id.clone();''',
    '''    let conversation_ref = match normalize_conversation_ref(intent.conversation_ref.as_deref()) {
        Ok(value) => value,
        Err(()) => return error_response(StatusCode::BAD_REQUEST, "invalid_blackboard_intent"),
    };
    let intent_id = match execution::github_intent_id(
        intent.intent_id.as_deref(),
        event.repository.id,
        event.issue.number,
    ) {
        Ok(value) => value,
        Err(()) => return error_response(StatusCode::BAD_REQUEST, "invalid_blackboard_intent"),
    };
    let delivery_id = execution::github_delivery_id(event.repository.id, event.issue.number);

    let participant_id = intent.participant_id.clone();''',
))

replacements.append((
    '''    let reply_to = intent.reply_to;
    let nonce = format!(
        "github:{}:issue:{}",
        event.repository.id, event.issue.number
    );
    let request_hash = identity::hash_token(''',
    '''    let reply_to = intent.reply_to;
    // Preserve the legacy GitHub nonce when intent_id is omitted. An explicit
    // intent_id becomes the semantic idempotency key while delivery_id remains
    // transport-specific.
    let nonce = intent_id.clone();
    let request_hash = identity::hash_token(''',
))

replacements.append((
    '''    let response_conversation_ref = conversation_ref.clone();
    let db_path = state.db_path.clone();

    let write_result =''',
    '''    let intent_envelope = execution::IntentEnvelope {
        intent_id: intent_id.clone(),
        participant_id: participant_id.clone(),
        conversation_ref: conversation_ref.clone(),
        capability: execution::POST_MESSAGE_CAPABILITY.to_owned(),
        resource: channel.clone(),
        request_hash: request_hash.clone(),
    };
    let authority_context = execution::AuthorityContext {
        principal: execution::Principal {
            provider: GITHUB_PROVIDER.to_owned(),
            subject: owner_subject.clone(),
        },
        mechanism: "github-webhook-hmac-sha256".to_owned(),
    };
    let response_conversation_ref = conversation_ref.clone();
    let response_intent_id = intent_id.clone();
    let response_delivery_id = delivery_id.clone();
    let ingress = execution::IngressProvenance {
        delivery_id: delivery_id.clone(),
        intent_id: intent_id.clone(),
        transport: "github-webhook".to_owned(),
        external_ref: delivery_id,
        principal: authority_context.principal.clone(),
    };
    let db_path = state.db_path.clone();

    let write_result =''',
))

replacements.append((
    '''            Ok(
                match db::append_navigation_message_with_conversation(
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
                },
            )''',
    '''            let result = match db::append_navigation_message_with_conversation(
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
))

replacements.append((
    '''            "conversation_ref": response_conversation_ref,
            "github_user_id": event.sender.id,''',
    '''            "conversation_ref": response_conversation_ref,
            "intent_id": response_intent_id,
            "delivery_id": response_delivery_id,
            "github_user_id": event.sender.id,''',
))

replacements.append((
    '''struct BlackboardIssueIntent {
    participant_id: String,''',
    '''struct BlackboardIssueIntent {
    #[serde(default)]
    intent_id: Option<String>,
    participant_id: String,''',
))

for old, new in replacements:
    if old not in text:
        raise SystemExit(f"anchor not found:\n{old[:120]}")
    text = text.replace(old, new, 1)

webhook.write_text(text)
