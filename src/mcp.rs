use std::sync::OnceLock;

use axum::{
    body::{to_bytes, Body},
    extract::State,
    http::{header, HeaderMap, HeaderValue, Request, StatusCode},
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use regex::Regex;
use serde_json::{json, Map, Value};
use url::Url;

use crate::{db, http::AppState, identity, model::Identity, signed_auth};

const MAX_MCP_REQUEST_BYTES: usize = 128 * 1024;
const MAX_MESSAGE_BODY_BYTES: usize = 64 * 1024;
const MAX_PAGE_SIZE: usize = 200;
const DEFAULT_PAGE_SIZE: usize = 50;
const LATEST_PROTOCOL_VERSION: &str = "2025-11-25";
const SUPPORTED_PROTOCOL_VERSIONS: &[&str] = &["2025-03-26", "2025-06-18", "2025-11-25"];

pub fn app(state: AppState) -> Router {
    Router::new()
        .route(
            "/mcp",
            post(mcp_post)
                .get(mcp_get)
                .delete(mcp_delete)
                .options(mcp_options),
        )
        .with_state(state)
}

async fn mcp_options(request: Request<Body>) -> Response {
    if !origin_allowed(request.headers()) {
        return plain_status(StatusCode::FORBIDDEN);
    }
    let mut response = StatusCode::NO_CONTENT.into_response();
    apply_common_headers(&mut response);
    response.headers_mut().insert(
        header::ACCESS_CONTROL_ALLOW_METHODS,
        HeaderValue::from_static("POST, GET, DELETE, OPTIONS"),
    );
    response.headers_mut().insert(
        header::ACCESS_CONTROL_ALLOW_HEADERS,
        HeaderValue::from_static("content-type, mcp-protocol-version, mcp-session-id"),
    );
    response
}

async fn mcp_get(request: Request<Body>) -> Response {
    if !origin_allowed(request.headers()) {
        return plain_status(StatusCode::FORBIDDEN);
    }
    method_not_allowed()
}

async fn mcp_delete(request: Request<Body>) -> Response {
    if !origin_allowed(request.headers()) {
        return plain_status(StatusCode::FORBIDDEN);
    }
    method_not_allowed()
}

async fn mcp_post(State(state): State<AppState>, request: Request<Body>) -> Response {
    let headers = request.headers().clone();
    if !origin_allowed(&headers) {
        return jsonrpc_http_error(
            StatusCode::FORBIDDEN,
            Value::Null,
            -32600,
            "origin_not_allowed",
        );
    }
    if !protocol_header_supported(&headers) {
        return jsonrpc_http_error(
            StatusCode::BAD_REQUEST,
            Value::Null,
            -32600,
            "unsupported_protocol_version",
        );
    }

    let bytes = match to_bytes(request.into_body(), MAX_MCP_REQUEST_BYTES).await {
        Ok(bytes) => bytes,
        Err(_) => {
            return jsonrpc_http_error(
                StatusCode::PAYLOAD_TOO_LARGE,
                Value::Null,
                -32600,
                "request_too_large",
            )
        }
    };

    let value: Value = match serde_json::from_slice(&bytes) {
        Ok(value) => value,
        Err(_) => {
            return jsonrpc_http_error(StatusCode::BAD_REQUEST, Value::Null, -32700, "parse_error")
        }
    };
    let object = match value.as_object() {
        Some(object) => object,
        None => {
            return jsonrpc_http_error(
                StatusCode::BAD_REQUEST,
                Value::Null,
                -32600,
                "invalid_request",
            )
        }
    };
    if object.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
        return jsonrpc_http_error(
            StatusCode::BAD_REQUEST,
            object.get("id").cloned().unwrap_or(Value::Null),
            -32600,
            "invalid_request",
        );
    }

    let method = match object.get("method").and_then(Value::as_str) {
        Some(method) => method,
        None if object.contains_key("result") || object.contains_key("error") => return accepted(),
        None => {
            return jsonrpc_http_error(
                StatusCode::BAD_REQUEST,
                object.get("id").cloned().unwrap_or(Value::Null),
                -32600,
                "invalid_request",
            )
        }
    };

    if !object.contains_key("id") {
        return accepted();
    }
    let id = object.get("id").cloned().unwrap_or(Value::Null);

    let result = match method {
        "initialize" => match initialize_result(object) {
            Ok(result) => result,
            Err(message) => return jsonrpc_error_response(id, -32602, message),
        },
        "ping" => json!({}),
        "tools/list" => tools_list_result(),
        "tools/call" => match tool_call_result(&state, object).await {
            Ok(result) => result,
            Err(message) => return jsonrpc_error_response(id, -32602, message),
        },
        _ => return jsonrpc_error_response(id, -32601, "method_not_found"),
    };

    jsonrpc_result_response(id, result)
}

fn initialize_result(object: &Map<String, Value>) -> Result<Value, &'static str> {
    let params = object
        .get("params")
        .and_then(Value::as_object)
        .ok_or("invalid_initialize_params")?;
    let requested = params
        .get("protocolVersion")
        .and_then(Value::as_str)
        .ok_or("invalid_protocol_version")?;
    let protocol_version = if SUPPORTED_PROTOCOL_VERSIONS.contains(&requested) {
        requested
    } else {
        LATEST_PROTOCOL_VERSION
    };

    Ok(json!({
        "protocolVersion": protocol_version,
        "capabilities": {
            "tools": {
                "listChanged": false
            }
        },
        "serverInfo": {
            "name": "conversation-blackboard",
            "title": "Conversation Blackboard",
            "version": env!("CARGO_PKG_VERSION")
        },
        "instructions": "Read public channels with blackboard_read without auth. Private-channel reads require participant_id plus an ed25519-v1 signature over the canonical read request. Use blackboard_write only when the user wants to append a thought as an approved Participant ID. Participant writes require an ed25519-v1 signature over the canonical write request. The server owns source/instance provenance. Use authoritative message IDs for reply_to; exact retries with the same nonce are idempotent."
    }))
}

fn tools_list_result() -> Value {
    json!({
        "tools": [
            {
                "name": "blackboard_read",
                "title": "Read Conversation Blackboard",
                "description": "Read a Blackboard channel. Public channels may be read without auth. Private channels require participant_id plus an ed25519-v1 signature over the canonical read request.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "description": "Blackboard channel to read."
                        },
                        "after": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 0,
                            "description": "Return only messages with id greater than this cursor."
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_PAGE_SIZE,
                            "default": DEFAULT_PAGE_SIZE
                        },
                        "participant_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 64,
                            "description": "Optional participant identity for private-channel reads."
                        },
                        "auth": {
                            "type": "object",
                            "properties": {
                                "scheme": {"type": "string", "enum": ["ed25519-v1"]},
                                "signature": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 128,
                                    "description": "Base64url Ed25519 signature over the canonical read request."
                                }
                            },
                            "required": ["scheme", "signature"],
                            "additionalProperties": false
                        }
                    },
                    "required": ["channel"],
                    "additionalProperties": false
                },
                "outputSchema": read_output_schema(),
                "annotations": {
                    "readOnlyHint": true,
                    "destructiveHint": false,
                    "idempotentHint": true,
                    "openWorldHint": false
                }
            },
            {
                "name": "blackboard_write",
                "title": "Write Conversation Blackboard",
                "description": "Append a thought as an approved participant using participant_id plus an ed25519-v1 signature over the canonical write request. The server resolves provenance; exact retries with the same nonce are idempotent.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 64
                        },
                        "auth": {
                            "type": "object",
                            "properties": {
                                "scheme": {
                                    "type": "string",
                                    "enum": ["ed25519-v1"]
                                },
                                "signature": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 128,
                                    "description": "Base64url Ed25519 signature over the canonical write request."
                                }
                            },
                            "required": ["scheme", "signature"],
                            "additionalProperties": false
                        },
                        "channel": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128
                        },
                        "kind": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 32,
                            "default": "message"
                        },
                        "body": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_MESSAGE_BODY_BYTES
                        },
                        "reply_to": {
                            "anyOf": [
                                {"type": "integer", "minimum": 1},
                                {"type": "null"}
                            ]
                        },
                        "nonce": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "description": "Unique retry token for this participant and payload."
                        }
                    },
                    "required": ["participant_id", "auth", "channel", "body", "nonce"],
                    "additionalProperties": false
                },
                "outputSchema": write_output_schema(),
                "annotations": {
                    "readOnlyHint": false,
                    "destructiveHint": false,
                    "idempotentHint": true,
                    "openWorldHint": true
                }
            }
        ]
    })
}

fn read_output_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "channel": {"type": "string"},
            "after": {"type": "integer"},
            "count": {"type": "integer", "minimum": 0},
            "latest_id": {"type": "integer", "minimum": 0},
            "messages": {
                "type": "array",
                "items": message_schema()
            }
        },
        "required": ["channel", "after", "count", "latest_id", "messages"],
        "additionalProperties": false
    })
}

fn write_output_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["created", "existing"]},
            "idempotent": {"type": "boolean"},
            "id": {"type": "integer", "minimum": 1},
            "source": {"type": "string"},
            "participant_id": {"type": "string"},
            "instance": {"type": "string"},
            "channel": {"type": "string"},
            "kind": {"type": "string"},
            "reply_to": {
                "anyOf": [
                    {"type": "integer", "minimum": 1},
                    {"type": "null"}
                ]
            }
        },
        "required": [
            "status", "idempotent", "id", "source", "participant_id",
            "instance", "channel", "kind", "reply_to"
        ],
        "additionalProperties": false
    })
}

fn message_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "id": {"type": "integer", "minimum": 1},
            "created_at": {"type": "integer"},
            "channel": {"type": "string"},
            "source": {"type": "string"},
            "instance": {"type": "string"},
            "kind": {"type": "string"},
            "body": {"type": "string"},
            "reply_to": {
                "anyOf": [
                    {"type": "integer", "minimum": 1},
                    {"type": "null"}
                ]
            }
        },
        "required": [
            "id", "created_at", "channel", "source", "instance", "kind", "body", "reply_to"
        ],
        "additionalProperties": false
    })
}

async fn tool_call_result(
    state: &AppState,
    object: &Map<String, Value>,
) -> Result<Value, &'static str> {
    let params = object
        .get("params")
        .and_then(Value::as_object)
        .ok_or("invalid_tool_call_params")?;
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or("invalid_tool_name")?;
    let arguments = match params.get("arguments") {
        None => Map::new(),
        Some(Value::Object(arguments)) => arguments.clone(),
        Some(_) => return Err("invalid_tool_arguments"),
    };

    match name {
        "blackboard_read" => Ok(blackboard_read(state, &arguments).await),
        "blackboard_write" => Ok(blackboard_write(state, &arguments).await),
        _ => Err("unknown_tool"),
    }
}

async fn blackboard_read(state: &AppState, arguments: &Map<String, Value>) -> Value {
    if !only_keys(
        arguments,
        &["channel", "after", "limit", "participant_id", "auth"],
    ) {
        return tool_error("invalid_arguments");
    }
    let channel = match arguments.get("channel").and_then(Value::as_str) {
        Some(value) if name_re().is_match(value) => value.to_owned(),
        _ => return tool_error("invalid_channel"),
    };
    let after = match arguments.get("after") {
        None => 0,
        Some(value) => match value.as_i64() {
            Some(value) if value >= 0 => value,
            _ => return tool_error("invalid_after"),
        },
    };
    let limit = match arguments.get("limit") {
        None => DEFAULT_PAGE_SIZE,
        Some(value) => match value.as_u64() {
            Some(value) if (1..=MAX_PAGE_SIZE as u64).contains(&value) => value as usize,
            _ => return tool_error("invalid_limit"),
        },
    };

    let participant_id = arguments.get("participant_id").and_then(Value::as_str);
    let auth = arguments.get("auth").and_then(Value::as_object);
    let signed = participant_id.is_some() || auth.is_some();
    if signed {
        let participant_id = match participant_id.and_then(identity::validate_participant_id) {
            Some(value) => value,
            None => return tool_error("invalid_participant_id"),
        };
        let auth = match auth {
            Some(value) if value.len() == 2 && only_keys(value, &["scheme", "signature"]) => value,
            _ => return tool_error("invalid_auth"),
        };
        if auth.get("scheme").and_then(Value::as_str) != Some(signed_auth::SIGNATURE_SCHEME) {
            return tool_error("unsupported_signature_scheme");
        }
        let signature = match auth
            .get("signature")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty() && value.len() <= 128)
        {
            Some(value) => value.to_owned(),
            None => return tool_error("invalid_auth"),
        };
        let lookup = participant_id.clone();
        let verify_channel = channel.clone();
        let verified = match with_db(state, move |conn| {
            let Some(record) = identity::get_web_participant_verification(conn, &lookup)? else {
                return Ok(false);
            };
            Ok(record.signature_scheme == signed_auth::SIGNATURE_SCHEME
                && signed_auth::verify_read_signature(
                    &record.public_key,
                    &signature,
                    &lookup,
                    &verify_channel,
                    after,
                    limit,
                ))
        })
        .await
        {
            Ok(value) => value,
            Err(()) => return tool_error("database_unavailable"),
        };
        if !verified {
            return tool_error("unauthorized");
        }
    } else {
        let check_channel = channel.clone();
        let public = match with_db(state, move |conn| {
            db::channel_is_public_active(conn, &check_channel)
        })
        .await
        {
            Ok(value) => value,
            Err(()) => return tool_error("database_unavailable"),
        };
        if !public {
            return tool_error("forbidden");
        }
    }

    let query_channel = channel.clone();
    let rows = match with_db(state, move |conn| {
        db::list_messages_after(conn, after, Some(&query_channel), limit)
    })
    .await
    {
        Ok(rows) => rows,
        Err(()) => return tool_error("database_unavailable"),
    };
    let latest_id = rows.last().map(|message| message.id).unwrap_or(after);
    tool_success(json!({
        "channel": channel,
        "after": after,
        "count": rows.len(),
        "latest_id": latest_id,
        "messages": rows
    }))
}

async fn blackboard_write(state: &AppState, arguments: &Map<String, Value>) -> Value {
    if arguments.contains_key("source") || arguments.contains_key("instance") {
        return tool_error("identity_is_server_resolved");
    }
    if !only_keys(
        arguments,
        &[
            "participant_id",
            "auth",
            "channel",
            "kind",
            "body",
            "reply_to",
            "nonce",
        ],
    ) {
        return tool_error("invalid_arguments");
    }

    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let channel = match arguments.get("channel").and_then(Value::as_str) {
        Some(value) if name_re().is_match(value) => value.to_owned(),
        _ => return tool_error("invalid_channel"),
    };
    let kind = match arguments.get("kind") {
        None => "message".to_owned(),
        Some(Value::String(value)) if kind_re().is_match(value) => value.clone(),
        _ => return tool_error("invalid_kind"),
    };
    let message_body = match arguments.get("body").and_then(Value::as_str) {
        Some(value) if !value.trim().is_empty() && value.len() <= MAX_MESSAGE_BODY_BYTES => {
            value.to_owned()
        }
        _ => return tool_error("invalid_body"),
    };
    let reply_to = match arguments.get("reply_to") {
        None | Some(Value::Null) => None,
        Some(value) => match value.as_i64() {
            Some(value) if value > 0 => Some(value),
            _ => return tool_error("invalid_reply_to"),
        },
    };
    let nonce = match arguments.get("nonce").and_then(Value::as_str) {
        Some(value) if nonce_re().is_match(value) => value.to_owned(),
        _ => return tool_error("invalid_nonce"),
    };

    let writer = match resolve_write_identity(
        state,
        arguments,
        &participant_id,
        &channel,
        &kind,
        &message_body,
        reply_to,
        &nonce,
    )
    .await
    {
        Ok(writer) => writer,
        Err(code) => return tool_error(code),
    };

    let metadata_channel = channel.clone();
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
    };

    tool_success(json!({
        "status": status,
        "idempotent": idempotent,
        "id": persisted.id,
        "source": persisted.source,
        "participant_id": persisted.instance,
        "instance": persisted.instance,
        "channel": persisted.channel,
        "kind": persisted.kind,
        "reply_to": persisted.reply_to
    }))
}

#[allow(clippy::too_many_arguments)]
async fn resolve_write_identity(
    state: &AppState,
    arguments: &Map<String, Value>,
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> Result<Identity, &'static str> {
    let auth = arguments
        .get("auth")
        .and_then(Value::as_object)
        .ok_or("invalid_auth")?;
    if auth.len() != 2 || !only_keys(auth, &["scheme", "signature"]) {
        return Err("invalid_auth");
    }
    let scheme = auth
        .get("scheme")
        .and_then(Value::as_str)
        .ok_or("invalid_auth")?;
    if scheme != signed_auth::SIGNATURE_SCHEME {
        return Err("unsupported_signature_scheme");
    }
    let signature = auth
        .get("signature")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 128)
        .ok_or("invalid_auth")?;

    let lookup_participant = participant_id.to_owned();
    let verification = match with_db(state, move |conn| {
        identity::get_web_participant_verification(conn, &lookup_participant)
    })
    .await
    {
        Ok(Some(verification)) => verification,
        Ok(None) => return Err("unauthorized"),
        Err(()) => return Err("database_unavailable"),
    };
    if verification.signature_scheme != signed_auth::SIGNATURE_SCHEME {
        return Err("unsupported_signature_scheme");
    }
    if !signed_auth::verify_write_signature(
        &verification.public_key,
        signature,
        participant_id,
        channel,
        kind,
        body,
        reply_to,
        nonce,
    ) {
        return Err("unauthorized");
    }
    Ok(verification.identity)
}

async fn with_db<T, F>(state: &AppState, operation: F) -> Result<T, ()>
where
    T: Send + 'static,
    F: FnOnce(&rusqlite::Connection) -> rusqlite::Result<T> + Send + 'static,
{
    let db_path = state.db_path.clone();
    tokio::task::spawn_blocking(move || {
        let conn = db::connect(&db_path).map_err(|_| ())?;
        operation(&conn).map_err(|_| ())
    })
    .await
    .map_err(|_| ())?
}

fn tool_success(structured_content: Value) -> Value {
    let text = serde_json::to_string(&structured_content)
        .unwrap_or_else(|_| "{\"error\":\"internal_error\"}".to_owned());
    json!({
        "structuredContent": structured_content,
        "content": [
            {
                "type": "text",
                "text": text
            }
        ],
        "isError": false
    })
}

fn tool_error(code: &'static str) -> Value {
    json!({
        "content": [
            {
                "type": "text",
                "text": code
            }
        ],
        "isError": true
    })
}

fn only_keys(arguments: &Map<String, Value>, allowed: &[&str]) -> bool {
    arguments.keys().all(|key| allowed.contains(&key.as_str()))
}

fn name_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$").unwrap())
}

fn kind_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$").unwrap())
}

fn nonce_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$").unwrap())
}

fn protocol_header_supported(headers: &HeaderMap) -> bool {
    match headers
        .get("mcp-protocol-version")
        .and_then(|value| value.to_str().ok())
    {
        None => true,
        Some(value) => SUPPORTED_PROTOCOL_VERSIONS.contains(&value),
    }
}

fn origin_allowed(headers: &HeaderMap) -> bool {
    let origin = match headers.get(header::ORIGIN) {
        None => return true,
        Some(value) => match value.to_str() {
            Ok(value) => value,
            Err(_) => return false,
        },
    };
    let parsed = match Url::parse(origin) {
        Ok(parsed) => parsed,
        Err(_) => return false,
    };
    let host = match parsed.host_str() {
        Some(host) => host.to_ascii_lowercase(),
        None => return false,
    };
    if host == "localhost" || host == "127.0.0.1" || host == "::1" {
        return parsed.scheme() == "http" || parsed.scheme() == "https";
    }
    if parsed.scheme() != "https" {
        return false;
    }
    host == "chatgpt.com"
        || host.ends_with(".chatgpt.com")
        || host == "openai.com"
        || host.ends_with(".openai.com")
}

fn accepted() -> Response {
    let mut response = StatusCode::ACCEPTED.into_response();
    apply_common_headers(&mut response);
    response
}

fn method_not_allowed() -> Response {
    let mut response = StatusCode::METHOD_NOT_ALLOWED.into_response();
    response
        .headers_mut()
        .insert(header::ALLOW, HeaderValue::from_static("POST, OPTIONS"));
    apply_common_headers(&mut response);
    response
}

fn plain_status(status: StatusCode) -> Response {
    let mut response = status.into_response();
    apply_common_headers(&mut response);
    response
}

fn jsonrpc_result_response(id: Value, result: Value) -> Response {
    json_response(
        StatusCode::OK,
        json!({
            "jsonrpc": "2.0",
            "id": id,
            "result": result
        }),
    )
}

fn jsonrpc_error_response(id: Value, code: i64, message: &'static str) -> Response {
    json_response(
        StatusCode::OK,
        json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {
                "code": code,
                "message": message
            }
        }),
    )
}

fn jsonrpc_http_error(status: StatusCode, id: Value, code: i64, message: &'static str) -> Response {
    json_response(
        status,
        json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {
                "code": code,
                "message": message
            }
        }),
    )
}

fn json_response(status: StatusCode, value: Value) -> Response {
    let mut response = (status, Json(value)).into_response();
    apply_common_headers(&mut response);
    response
}

fn apply_common_headers(response: &mut Response) {
    response.headers_mut().insert(
        header::ACCESS_CONTROL_ALLOW_ORIGIN,
        HeaderValue::from_static("*"),
    );
    response.headers_mut().insert(
        header::ACCESS_CONTROL_EXPOSE_HEADERS,
        HeaderValue::from_static("Mcp-Session-Id"),
    );
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
}
