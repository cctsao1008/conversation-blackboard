use std::sync::{Arc, OnceLock};

use axum::{
    body::{to_bytes, Body},
    extract::State,
    http::{header, HeaderMap, HeaderValue, Request, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use regex::Regex;
use serde_json::{json, Map, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use url::Url;

use crate::{
    adapter_profile, authorization, contract_schema, db, execution, http::AppState, identity,
    model::Identity, oidc, participant_auth,
};

const MAX_MCP_REQUEST_BYTES: usize = 128 * 1024;
const MAX_MESSAGE_BODY_BYTES: usize = 64 * 1024;
const MAX_PAGE_SIZE: usize = 200;
const DEFAULT_PAGE_SIZE: usize = 50;
const MODERN_PROTOCOL_VERSION: &str = "2026-07-28";
const LATEST_LEGACY_PROTOCOL_VERSION: &str = "2025-11-25";
const SUPPORTED_LEGACY_PROTOCOL_VERSIONS: &[&str] = &["2025-03-26", "2025-06-18", "2025-11-25"];
const SUPPORTED_PROTOCOL_VERSIONS: &[&str] =
    &["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"];

#[derive(Clone)]
struct McpHttpState {
    app: AppState,
    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,
    protected_resource: Option<ProtectedResourceConfig>,
}

#[derive(Clone, Debug)]
struct ProtectedResourceConfig {
    resource: String,
    metadata_path: String,
    metadata_url: String,
}

#[cfg(test)]
pub fn app(state: AppState) -> Router {
    app_with_oidc(state, None)
}

#[cfg(test)]
pub(crate) fn app_with_oidc(
    state: AppState,
    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,
) -> Router {
    app_with_remote_auth(state, oidc_verifier, None).expect("no MCP resource URL is always valid")
}

pub(crate) fn app_with_remote_auth(
    state: AppState,
    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,
    resource_url: Option<&str>,
) -> Result<Router, Box<dyn std::error::Error + Send + Sync>> {
    let protected_resource = match resource_url {
        Some(resource_url) => Some(ProtectedResourceConfig::parse(resource_url)?),
        None => None,
    };
    if protected_resource.is_some() && oidc_verifier.is_none() {
        return Err("BLACKBOARD_MCP_RESOURCE_URL requires OIDC configuration".into());
    }
    let state = McpHttpState {
        app: state,
        oidc_verifier,
        protected_resource: protected_resource.clone(),
    };
    let mut router = Router::new().route(
        "/mcp",
        post(mcp_post)
            .get(mcp_get)
            .delete(mcp_delete)
            .options(mcp_options),
    );
    if let Some(config) = protected_resource {
        router = router.route(&config.metadata_path, get(protected_resource_metadata));
    }
    Ok(router.with_state(state))
}

impl ProtectedResourceConfig {
    fn parse(resource: &str) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let url = Url::parse(resource)?;
        if url.scheme() != "https" || url.host_str().is_none() || url.fragment().is_some() {
            return Err(
                "BLACKBOARD_MCP_RESOURCE_URL must be an absolute HTTPS URL without a fragment"
                    .into(),
            );
        }
        if url.query().is_some() {
            return Err("BLACKBOARD_MCP_RESOURCE_URL must not contain a query".into());
        }
        let path = url.path();
        let suffix = if path == "/" {
            String::new()
        } else {
            path.to_owned()
        };
        let metadata_path = format!("/.well-known/oauth-protected-resource{suffix}");
        let mut metadata = url.clone();
        metadata.set_path(&metadata_path);
        metadata.set_query(None);
        metadata.set_fragment(None);
        Ok(Self {
            resource: url.to_string(),
            metadata_path,
            metadata_url: metadata.to_string(),
        })
    }
}

async fn protected_resource_metadata(State(state): State<McpHttpState>) -> Response {
    let Some(resource) = state.protected_resource.as_ref() else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let Some(verifier) = state.oidc_verifier.as_ref() else {
        return StatusCode::NOT_FOUND.into_response();
    };
    (
        [(header::CACHE_CONTROL, "public, max-age=300")],
        Json(json!({
            "resource": resource.resource,
            "authorization_servers": [verifier.issuer()],
            "bearer_methods_supported": ["header"]
        })),
    )
        .into_response()
}

/// Serve MCP over newline-delimited JSON-RPC on stdin/stdout.
///
/// Stdout is reserved exclusively for protocol responses. The stdio transport
/// does not create a separate identity model; tool calls retain the canonical
/// Blackboard participant-HMAC authentication and authorization path.
pub async fn serve_stdio(state: AppState) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    db::initialize(&state.db_path)?;
    let stdin = tokio::io::stdin();
    let mut lines = BufReader::new(stdin).lines();
    let mut stdout = tokio::io::stdout();

    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }
        let response = match serde_json::from_str::<Value>(&line) {
            Ok(value) => stdio_dispatch(&state, &value).await,
            Err(_) => Some(json!({
                "jsonrpc": "2.0",
                "id": Value::Null,
                "error": {"code": -32700, "message": "parse_error"}
            })),
        };
        if let Some(response) = response {
            let mut encoded = serde_json::to_vec(&response)?;
            encoded.push(b'\n');
            stdout.write_all(&encoded).await?;
            stdout.flush().await?;
        }
    }
    Ok(())
}

pub(crate) async fn stdio_dispatch(state: &AppState, value: &Value) -> Option<Value> {
    let object = match value.as_object() {
        Some(object) => object,
        None => {
            return Some(json!({
                "jsonrpc": "2.0",
                "id": Value::Null,
                "error": {"code": -32600, "message": "invalid_request"}
            }))
        }
    };
    let id = object.get("id").cloned().unwrap_or(Value::Null);
    if object.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
        return Some(json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": -32600, "message": "invalid_request"}
        }));
    }

    let method = match object.get("method").and_then(Value::as_str) {
        Some(method) => method,
        None if object.contains_key("result") || object.contains_key("error") => return None,
        None => {
            return Some(json!({
                "jsonrpc": "2.0",
                "id": id,
                "error": {"code": -32600, "message": "invalid_request"}
            }))
        }
    };

    // MCP notifications, including notifications/initialized, are one-way.
    if !object.contains_key("id") {
        return None;
    }

    let result = match method {
        "server/discover" => match validate_modern_request_metadata(object) {
            Ok(()) => Ok(discover_result()),
            Err(message) => Err((-32602, message)),
        },
        "initialize" => match initialize_result(object) {
            Ok(result) => Ok(result),
            Err(message) => Err((-32602, message)),
        },
        "ping" => Ok(json!({})),
        "tools/list" => Ok(tools_list_result()),
        "tools/call" => match tool_call_result(state, object, None).await {
            Ok(result) => Ok(result),
            Err(message) => Err((-32602, message)),
        },
        _ => Err((-32601, "method_not_found")),
    };

    Some(match result {
        Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
        Err((code, message)) => {
            json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
        }
    })
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
        HeaderValue::from_static(
            "content-type, accept, authorization, mcp-protocol-version, mcp-method, mcp-name, mcp-session-id",
        ),
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

async fn mcp_post(State(state): State<McpHttpState>, request: Request<Body>) -> Response {
    let headers = request.headers().clone();
    if !origin_allowed(&headers) {
        return jsonrpc_http_error(
            StatusCode::FORBIDDEN,
            Value::Null,
            -32600,
            "origin_not_allowed",
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
    let id = object.get("id").cloned().unwrap_or(Value::Null);
    if object.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
        return jsonrpc_http_error(StatusCode::BAD_REQUEST, id, -32600, "invalid_request");
    }

    let method = match object.get("method").and_then(Value::as_str) {
        Some(method) => method,
        None if object.contains_key("result") || object.contains_key("error") => return accepted(),
        None => return jsonrpc_http_error(StatusCode::BAD_REQUEST, id, -32600, "invalid_request"),
    };

    let transport_principal = match resolve_http_bearer_principal(&state, &headers) {
        Ok(principal) => principal,
        Err(()) => return bearer_unauthorized(id, state.protected_resource.as_ref()),
    };

    if state.oidc_verifier.is_some()
        && transport_principal.is_none()
        && http_tool_call_requires_auth(&state.app, object).await
    {
        return bearer_unauthorized(id, state.protected_resource.as_ref());
    }

    let body_protocol = request_protocol_version(object);
    let header_protocol = headers
        .get("mcp-protocol-version")
        .and_then(|value| value.to_str().ok());
    let modern = body_protocol.is_some()
        || header_protocol == Some(MODERN_PROTOCOL_VERSION)
        || method == "server/discover";

    if modern {
        if let Err(message) = validate_modern_http_metadata(&headers, object, method) {
            return jsonrpc_http_error(StatusCode::BAD_REQUEST, id, -32020, message);
        }
        let requested = body_protocol.expect("validated modern request has protocol version");
        if requested != MODERN_PROTOCOL_VERSION {
            return unsupported_protocol_version_response(id, requested);
        }

        if !object.contains_key("id") {
            return accepted();
        }

        let result = match method {
            "server/discover" => discover_result(),
            "tools/list" => modern_result(
                method,
                tools_list_result_for_modern_http(state.oidc_verifier.is_some()),
            ),
            "tools/call" => {
                match tool_call_result(&state.app, object, transport_principal.as_ref()).await {
                    Ok(result) => modern_result(method, result),
                    Err(message) => return jsonrpc_error_response(id, -32602, message),
                }
            }
            _ => return jsonrpc_http_error(StatusCode::NOT_FOUND, id, -32601, "method_not_found"),
        };
        return jsonrpc_result_response(id, result);
    }

    if let Some(version) = header_protocol {
        if !SUPPORTED_LEGACY_PROTOCOL_VERSIONS.contains(&version) {
            return unsupported_protocol_version_response(id, version);
        }
    }

    if !object.contains_key("id") {
        return accepted();
    }

    let result = match method {
        "initialize" => match initialize_result(object) {
            Ok(result) => result,
            Err(message) => return jsonrpc_error_response(id, -32602, message),
        },
        "ping" => json!({}),
        "tools/list" => tools_list_result(),
        "tools/call" => {
            match tool_call_result(&state.app, object, transport_principal.as_ref()).await {
                Ok(result) => result,
                Err(message) => return jsonrpc_error_response(id, -32602, message),
            }
        }
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
    let protocol_version = if SUPPORTED_LEGACY_PROTOCOL_VERSIONS.contains(&requested) {
        requested
    } else {
        LATEST_LEGACY_PROTOCOL_VERSION
    };

    Ok(json!({
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {"listChanged": false}},
        "serverInfo": {
            "name": "conversation-blackboard",
            "title": "Conversation Blackboard",
            "version": env!("CARGO_PKG_VERSION")
        },
        "instructions": "MCP is an adapter over Blackboard domain semantics. Use blackboard_read / blackboard_write for messages, blackboard_access_context for effective grants, blackboard_execution_receipt for semantic execution read-back, blackboard_execution_audit for immutable historical audit, and blackboard_execution_audit_integrity for structural audit verification. Use blackboard_execution_audit_sweep only with privileged corpus-wide audit authority, and blackboard_authorization_policy_integrity only with privileged global policy-integrity authority. Authenticated calls use hmac-sha256-v1 proofs bound to the canonical request."
    }))
}

fn discover_result() -> Value {
    json!({
        "resultType": "complete",
        "supportedVersions": SUPPORTED_PROTOCOL_VERSIONS,
        "capabilities": {"tools": {"listChanged": false}},
        "_meta": {
            "io.modelcontextprotocol/serverInfo": {
                "name": "conversation-blackboard",
                "version": env!("CARGO_PKG_VERSION")
            }
        },
        "instructions": "Conversation Blackboard exposes MCP as a protocol projection. Blackboard remains authoritative for identity, authorization, semantic execution, durable attribution, and audit.",
        "ttlMs": 300000,
        "cacheScope": "public"
    })
}

fn modern_result(method: &str, mut result: Value) -> Value {
    let Some(object) = result.as_object_mut() else {
        return result;
    };
    object.insert("resultType".to_owned(), json!("complete"));
    let meta = object
        .entry("_meta".to_owned())
        .or_insert_with(|| json!({}));
    if let Some(meta) = meta.as_object_mut() {
        meta.insert(
            "io.modelcontextprotocol/serverInfo".to_owned(),
            json!({
                "name": "conversation-blackboard",
                "version": env!("CARGO_PKG_VERSION")
            }),
        );
    }
    if method == "tools/list" {
        object.insert("ttlMs".to_owned(), json!(300000));
        object.insert("cacheScope".to_owned(), json!("public"));
    }
    result
}

async fn http_tool_call_requires_auth(state: &AppState, object: &Map<String, Value>) -> bool {
    if object.get("method").and_then(Value::as_str) != Some("tools/call") {
        return false;
    }
    let Some(params) = object.get("params").and_then(Value::as_object) else {
        return false;
    };
    let Some(name) = params.get("name").and_then(Value::as_str) else {
        return false;
    };
    let arguments = match params.get("arguments") {
        None => None,
        Some(Value::Object(arguments)) => Some(arguments),
        Some(_) => return false,
    };
    if arguments.is_some_and(|arguments| arguments.contains_key("auth")) {
        return false;
    }
    match name {
        "blackboard_write"
        | "blackboard_access_context"
        | "blackboard_execution_receipt"
        | "blackboard_execution_audit"
        | "blackboard_execution_audit_integrity"
        | "blackboard_execution_audit_sweep"
        | "blackboard_authorization_policy_integrity"
        | "blackboard_authorization_policy" => true,
        "blackboard_read" => {
            let Some(arguments) = arguments else {
                return false;
            };
            if arguments.get("participant_id").is_some() {
                return true;
            }
            let Some(channel) = arguments.get("channel").and_then(Value::as_str) else {
                return false;
            };
            if !name_re().is_match(channel) {
                return false;
            }
            let channel = channel.to_owned();
            match with_db(state, move |conn| {
                db::channel_is_public_active(conn, &channel)
            })
            .await
            {
                Ok(public) => !public,
                Err(()) => false,
            }
        }
        _ => false,
    }
}

fn request_protocol_version(object: &Map<String, Value>) -> Option<&str> {
    object
        .get("params")?
        .as_object()?
        .get("_meta")?
        .as_object()?
        .get("io.modelcontextprotocol/protocolVersion")?
        .as_str()
}

fn validate_modern_request_metadata(object: &Map<String, Value>) -> Result<(), &'static str> {
    let params = object
        .get("params")
        .and_then(Value::as_object)
        .ok_or("invalid_request_metadata")?;
    let meta = params
        .get("_meta")
        .and_then(Value::as_object)
        .ok_or("invalid_request_metadata")?;
    let protocol = meta
        .get("io.modelcontextprotocol/protocolVersion")
        .and_then(Value::as_str)
        .ok_or("invalid_request_metadata")?;
    if protocol != MODERN_PROTOCOL_VERSION {
        return Err("unsupported_protocol_version");
    }
    if let Some(client_info) = meta.get("io.modelcontextprotocol/clientInfo") {
        let client_info = client_info.as_object().ok_or("invalid_request_metadata")?;
        if client_info
            .get("name")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .is_none()
            || client_info
                .get("version")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .is_none()
        {
            return Err("invalid_request_metadata");
        }
    }
    if !meta
        .get("io.modelcontextprotocol/clientCapabilities")
        .is_some_and(Value::is_object)
    {
        return Err("invalid_request_metadata");
    }
    Ok(())
}

fn validate_modern_http_metadata(
    headers: &HeaderMap,
    object: &Map<String, Value>,
    method: &str,
) -> Result<(), &'static str> {
    let body_protocol = request_protocol_version(object).ok_or("header_mismatch")?;
    let header_protocol = headers
        .get("mcp-protocol-version")
        .and_then(|value| value.to_str().ok())
        .ok_or("header_mismatch")?;
    if header_protocol != body_protocol {
        return Err("header_mismatch");
    }

    let header_method = headers
        .get("mcp-method")
        .and_then(|value| value.to_str().ok())
        .ok_or("header_mismatch")?;
    if header_method != method {
        return Err("header_mismatch");
    }

    if method == "tools/call" {
        let body_name = object
            .get("params")
            .and_then(Value::as_object)
            .and_then(|params| params.get("name"))
            .and_then(Value::as_str)
            .ok_or("header_mismatch")?;
        let header_name = headers
            .get("mcp-name")
            .and_then(|value| value.to_str().ok())
            .and_then(decode_mcp_header_value)
            .ok_or("header_mismatch")?;
        if header_name != body_name {
            return Err("header_mismatch");
        }
    }

    let params = object
        .get("params")
        .and_then(Value::as_object)
        .ok_or("header_mismatch")?;
    let meta = params
        .get("_meta")
        .and_then(Value::as_object)
        .ok_or("header_mismatch")?;
    if let Some(client_info) = meta.get("io.modelcontextprotocol/clientInfo") {
        let client_info = client_info.as_object().ok_or("header_mismatch")?;
        if client_info
            .get("name")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .is_none()
            || client_info
                .get("version")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .is_none()
        {
            return Err("header_mismatch");
        }
    }
    if !meta
        .get("io.modelcontextprotocol/clientCapabilities")
        .is_some_and(Value::is_object)
    {
        return Err("header_mismatch");
    }
    Ok(())
}

fn decode_mcp_header_value(value: &str) -> Option<String> {
    const PREFIX: &str = "=?base64?";
    const SUFFIX: &str = "?=";
    if value.starts_with(PREFIX) && value.ends_with(SUFFIX) {
        let encoded = &value[PREFIX.len()..value.len() - SUFFIX.len()];
        let decoded = BASE64_STANDARD.decode(encoded).ok()?;
        return String::from_utf8(decoded).ok();
    }
    if value.is_empty()
        || value.trim() != value
        || !value
            .bytes()
            .all(|byte| byte == b'\t' || byte == b' ' || (0x21..=0x7e).contains(&byte))
    {
        return None;
    }
    Some(value.to_owned())
}

fn unsupported_protocol_version_response(id: Value, requested: &str) -> Response {
    jsonrpc_http_error_data(
        StatusCode::BAD_REQUEST,
        id,
        -32022,
        "unsupported_protocol_version",
        json!({
            "supported": SUPPORTED_PROTOCOL_VERSIONS,
            "requested": requested
        }),
    )
}

fn auth_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "scheme": {"type": "string", "enum": [participant_auth::AUTH_SCHEME]},
            "proof": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": "Unpadded base64url HMAC-SHA256 proof over the canonical request."
            }
        },
        "required": ["scheme", "proof"],
        "additionalProperties": false
    })
}

fn tools_list_result_for_modern_http(bearer_capable: bool) -> Value {
    let mut result = tools_list_result();
    if !bearer_capable {
        return result;
    }
    let Some(tools) = result.get_mut("tools").and_then(Value::as_array_mut) else {
        return result;
    };
    for tool in tools {
        let Some(tool_object) = tool.as_object_mut() else {
            continue;
        };
        if let Some(description) = tool_object.get("description").and_then(Value::as_str) {
            tool_object.insert(
                "description".to_owned(),
                json!(format!(
                    "{description} Remote HTTP callers may alternatively authenticate with Authorization: Bearer; participant-HMAC auth is required only when no verified Bearer principal is present."
                )),
            );
        }
        let Some(schema) = tool_object
            .get_mut("inputSchema")
            .and_then(Value::as_object_mut)
        else {
            continue;
        };
        if let Some(required) = schema.get_mut("required").and_then(Value::as_array_mut) {
            required.retain(|field| field.as_str() != Some("auth"));
        }
        if let Some(auth) = schema
            .get_mut("properties")
            .and_then(Value::as_object_mut)
            .and_then(|properties| properties.get_mut("auth"))
            .and_then(Value::as_object_mut)
        {
            auth.insert(
                "description".to_owned(),
                json!("Participant-HMAC fallback credential. Omit when this remote HTTP request is authenticated with a verified Authorization: Bearer token."),
            );
        }
    }
    result
}

fn tools_list_result() -> Value {
    json!({
        "tools": [
            {
                "name": "blackboard_read",
                "title": "Read Conversation Blackboard",
                "description": "Read a Blackboard channel. Public channels may be read without auth. Private channels require participant_id plus an hmac-sha256-v1 proof over the canonical read request.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "minLength": 1, "maxLength": 128},
                        "after": {"type": "integer", "minimum": 0, "default": 0},
                        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE, "default": DEFAULT_PAGE_SIZE},
                        "participant_id": contract_schema::participant_id_schema(),
                        "auth": auth_schema()
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
                "description": "Append a thought as an approved participant using participant_id plus an hmac-sha256-v1 proof over the canonical write request. The server resolves provenance; exact retries with the same nonce are idempotent.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "auth": auth_schema(),
                        "channel": {"type": "string", "minLength": 1, "maxLength": 128},
                        "kind": {"type": "string", "minLength": 1, "maxLength": 32, "default": "message"},
                        "body": {"type": "string", "minLength": 1, "maxLength": MAX_MESSAGE_BODY_BYTES},
                        "reply_to": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
                        "nonce": {"type": "string", "minLength": 1, "maxLength": 128}
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
            },
            {
                "name": "blackboard_access_context",
                "title": "Read Blackboard Access Context",
                "description": "Return the authenticated participant's effective capability grants without exposing credentials.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::access_context_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            },
            {
                "name": "blackboard_execution_receipt",
                "title": "Read Blackboard Execution Receipt",
                "description": "Read back the durable semantic execution receipt for one authenticated participant intent.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "intent_id": contract_schema::intent_id_schema(),
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "intent_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::execution_receipt_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            },
            {
                "name": "blackboard_execution_audit",
                "title": "Read Blackboard Execution Audit",
                "description": "Read immutable committed execution evidence without re-evaluating historical policy.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "intent_id": contract_schema::intent_id_schema(),
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "intent_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::execution_audit_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
                        },
            {
                "name": "blackboard_execution_audit_integrity",
                "title": "Verify Blackboard Execution Audit Integrity",
                "description": "Verify structural consistency of committed execution evidence without re-evaluating policy or repairing history.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "intent_id": contract_schema::intent_id_schema(),
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "intent_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::execution_audit_integrity_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            },
            {
                "name": "blackboard_execution_audit_sweep",
                "title": "Verify Blackboard Execution Audit Corpus",
                "description": "Run the privileged canonical database-wide execution audit integrity sweep without repairing history.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::execution_audit_sweep_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            },
            {
                "name": "blackboard_authorization_policy_integrity",
                "title": "Verify Blackboard Authorization Policy Integrity",
                "description": "Run the privileged canonical read-only integrity audit over durable and delegated authorization policy objects without repairing policy state.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::authorization_integrity_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            },
            {
                "name": "blackboard_authorization_policy",
                "title": "Read Blackboard Authorization Policy",
                "description": "Read the privileged canonical inventory of explicit durable and delegated authorization objects without modifying, normalizing, repairing, or consuming authority.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::authorization_policy_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
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
            "messages": {"type": "array", "items": contract_schema::message_schema()}
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
            "reply_to": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
        },
        "required": ["status", "idempotent", "id", "source", "participant_id", "instance", "channel", "kind", "reply_to"],
        "additionalProperties": false
    })
}

async fn tool_call_result(
    state: &AppState,
    object: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
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
        "blackboard_read" => Ok(blackboard_read(state, &arguments, transport_principal).await),
        "blackboard_write" => Ok(blackboard_write(state, &arguments, transport_principal).await),
        "blackboard_access_context" => {
            Ok(blackboard_access_context(state, &arguments, transport_principal).await)
        }
        "blackboard_execution_receipt" => {
            Ok(blackboard_execution_receipt(state, &arguments, transport_principal).await)
        }
        "blackboard_execution_audit" => {
            Ok(blackboard_execution_audit(state, &arguments, transport_principal).await)
        }
        "blackboard_execution_audit_integrity" => {
            Ok(blackboard_execution_audit_integrity(state, &arguments, transport_principal).await)
        }
        "blackboard_execution_audit_sweep" => {
            Ok(blackboard_execution_audit_sweep(state, &arguments, transport_principal).await)
        }
        "blackboard_authorization_policy_integrity" => Ok(
            blackboard_authorization_policy_integrity(state, &arguments, transport_principal).await,
        ),
        "blackboard_authorization_policy" => {
            Ok(blackboard_authorization_policy(state, &arguments, transport_principal).await)
        }
        _ => Err("unknown_tool"),
    }
}

fn parse_auth(arguments: &Map<String, Value>) -> Result<(&str, &str), &'static str> {
    let auth = arguments
        .get("auth")
        .and_then(Value::as_object)
        .ok_or("invalid_auth")?;
    if auth.len() != 2 || !only_keys(auth, &["scheme", "proof"]) {
        return Err("invalid_auth");
    }
    let scheme = auth
        .get("scheme")
        .and_then(Value::as_str)
        .ok_or("invalid_auth")?;
    if scheme != participant_auth::AUTH_SCHEME {
        return Err("unsupported_auth_scheme");
    }
    let proof = auth
        .get("proof")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 128)
        .ok_or("invalid_auth")?;
    Ok((scheme, proof))
}

async fn resolve_capability_identity(
    state: &AppState,
    arguments: &Map<String, Value>,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
) -> Result<Identity, &'static str> {
    let (_, proof) = parse_auth(arguments)?;
    let proof = proof.to_owned();
    let lookup = participant_id.to_owned();
    let auth = match with_db(state, move |conn| {
        identity::get_web_participant_auth(conn, &lookup)
    })
    .await
    {
        Ok(Some(auth)) => auth,
        Ok(None) => return Err("unauthorized"),
        Err(()) => return Err("database_unavailable"),
    };
    if auth.auth_scheme != participant_auth::AUTH_SCHEME {
        return Err("unsupported_auth_scheme");
    }
    if !participant_auth::verify_capability_proof(
        &auth.auth_secret,
        &proof,
        participant_id,
        capability,
        resource,
    ) {
        return Err("unauthorized");
    }
    Ok(auth.identity)
}

async fn resolve_capability_principal(
    state: &AppState,
    arguments: &Map<String, Value>,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    transport_principal: Option<&execution::Principal>,
) -> Result<execution::Principal, &'static str> {
    if let Some(principal) = transport_principal {
        let principal = principal.clone();
        let policy_principal = principal.clone();
        let participant = participant_id.to_owned();
        let capability = capability.to_owned();
        let resource = resource.map(str::to_owned);
        let allowed = with_db(state, move |conn| {
            authorization::authorize(
                conn,
                &policy_principal,
                &participant,
                &capability,
                resource.as_deref(),
            )
        })
        .await
        .map_err(|_| "database_unavailable")?;
        if !allowed {
            return Err("forbidden");
        }
        return Ok(principal);
    }

    resolve_capability_identity(state, arguments, participant_id, capability, resource).await?;
    Ok(execution::Principal {
        provider: "participant-hmac".to_owned(),
        subject: participant_id.to_owned(),
    })
}

async fn blackboard_access_context(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let principal = if let Some(principal) = transport_principal {
        principal.clone()
    } else {
        if let Err(code) =
            resolve_capability_identity(state, arguments, &participant_id, "access_context", None)
                .await
        {
            return tool_error(code);
        }
        execution::Principal {
            provider: "participant-hmac".to_owned(),
            subject: participant_id.clone(),
        }
    };
    let principal_for_grants = principal.clone();
    let lookup = participant_id.clone();
    let grants = match with_db(state, move |conn| {
        authorization::effective_grants(conn, &principal_for_grants, &lookup)
    })
    .await
    {
        Ok(grants) => grants,
        Err(()) => return tool_error("database_unavailable"),
    };
    if transport_principal.is_some() && grants.is_empty() {
        return tool_error("forbidden");
    }
    let mut capabilities = grants
        .iter()
        .map(|grant| grant.capability.clone())
        .collect::<Vec<_>>();
    capabilities.sort();
    capabilities.dedup();
    tool_success(json!({
        "participant_id": participant_id,
        "principal": principal,
        "capabilities": capabilities,
        "grants": grants,
        "adapter": adapter_profile::MCP.name
    }))
}

async fn blackboard_execution_receipt(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "intent_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let intent_id = match arguments.get("intent_id").and_then(Value::as_str) {
        Some(value) => match execution::normalize_intent_id(value) {
            Ok(value) => value,
            Err(()) => return tool_error("invalid_intent_id"),
        },
        None => return tool_error("invalid_intent_id"),
    };
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_RECEIPT,
        Some(&intent_id),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let receipt = match with_db(state, move |conn| {
        if !authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_RECEIPT,
            Some(&lookup_intent),
        )? {
            return Ok(None);
        }
        execution::get_execution_receipt(conn, &lookup_participant, &lookup_intent)
    })
    .await
    {
        Ok(Some(receipt)) => receipt,
        Ok(None) => return tool_error("execution_not_found"),
        Err(()) => return tool_error("database_unavailable"),
    };
    tool_success(json!({"execution": receipt}))
}

async fn blackboard_execution_audit(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "intent_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let intent_id = match arguments.get("intent_id").and_then(Value::as_str) {
        Some(value) => match execution::normalize_intent_id(value) {
            Ok(value) => value,
            Err(()) => return tool_error("invalid_intent_id"),
        },
        None => return tool_error("invalid_intent_id"),
    };
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_AUDIT,
        Some(&intent_id),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let audit = match with_db(state, move |conn| {
        if !authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )? {
            return Ok(None);
        }
        execution::get_execution_audit_bundle(conn, &lookup_participant, &lookup_intent)
    })
    .await
    {
        Ok(Some(audit)) => audit,
        Ok(None) => return tool_error("execution_not_found"),
        Err(()) => return tool_error("database_unavailable"),
    };
    tool_success(json!({"audit": audit}))
}

async fn blackboard_execution_audit_integrity(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "intent_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let intent_id = match arguments.get("intent_id").and_then(Value::as_str) {
        Some(value) => match execution::normalize_intent_id(value) {
            Ok(value) => value,
            Err(()) => return tool_error("invalid_intent_id"),
        },
        None => return tool_error("invalid_intent_id"),
    };
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_AUDIT,
        Some(&intent_id),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let report = match with_db(state, move |conn| {
        if !authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )? {
            return Ok(None);
        }
        Ok(Some(execution::verify_execution_audit_integrity(
            conn,
            &lookup_participant,
            &lookup_intent,
        )?))
    })
    .await
    {
        Ok(Some(report)) => report,
        Ok(None) => return tool_error("forbidden"),
        Err(()) => return tool_error("database_unavailable"),
    };
    tool_success(json!({"integrity": report}))
}

async fn blackboard_execution_audit_sweep(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_AUDIT_SWEEP,
        Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let report = match with_db(state, move |conn| {
        if !authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT_SWEEP,
            Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
        )? {
            return Ok(None);
        }
        Ok(Some(execution::sweep_execution_audit_integrity(conn)?))
    })
    .await
    {
        Ok(Some(report)) => report,
        Ok(None) => return tool_error("forbidden"),
        Err(()) => return tool_error("database_unavailable"),
    };
    tool_success(json!({"sweep": report}))
}

async fn blackboard_authorization_policy(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };

    // Authenticate first without evaluating policy. This keeps stale-schema
    // requests observationally read-only even though the shared authorization
    // evaluator has a compatibility ensure path.
    let principal = if let Some(principal) = transport_principal {
        principal.clone()
    } else {
        if let Err(code) = resolve_capability_identity(
            state,
            arguments,
            &participant_id,
            authorization::READ_AUTHORIZATION_POLICY,
            Some(authorization::AUTHORIZATION_POLICY_RESOURCE),
        )
        .await
        {
            return tool_error(code);
        }
        execution::Principal {
            provider: "participant-hmac".to_owned(),
            subject: participant_id.clone(),
        }
    };

    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (schema_current, allowed, snapshot) = match with_db(state, move |conn| {
        let schema_current = authorization::authorization_policy_snapshot_schema_current(conn)?;
        if !schema_current {
            return Ok((false, false, None));
        }
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY,
            Some(authorization::AUTHORIZATION_POLICY_RESOURCE),
        )?;
        if !allowed {
            return Ok((true, false, None));
        }
        Ok((
            true,
            true,
            Some(authorization::read_authorization_policy_snapshot(conn)?),
        ))
    })
    .await
    {
        Ok(value) => value,
        Err(()) => return tool_error("database_unavailable"),
    };
    if !schema_current {
        return tool_error("authorization_schema_not_current");
    }
    if !allowed {
        return tool_error("forbidden");
    }
    let snapshot = snapshot.expect("authorized current-schema policy read must produce snapshot");
    tool_success(json!({"policy": snapshot}))
}

async fn blackboard_authorization_policy_integrity(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
        Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (schema_current, report) = match with_db(state, move |conn| {
        let schema_current = authorization::authorization_integrity_schema_current(conn)?;
        if !schema_current {
            return Ok((false, None));
        }
        if !authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        )? {
            return Ok((true, None));
        }
        Ok((
            true,
            Some(authorization::audit_authorization_integrity(conn)?),
        ))
    })
    .await
    {
        Ok(value) => value,
        Err(()) => return tool_error("database_unavailable"),
    };
    if !schema_current {
        return tool_error("authorization_schema_not_current");
    }
    let Some(report) = report else {
        return tool_error("forbidden");
    };
    tool_success(json!({"integrity": report}))
}

async fn blackboard_read(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
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
    let has_auth = arguments.get("auth").is_some();
    if participant_id.is_some() || has_auth {
        let participant_id = match participant_id.and_then(identity::validate_participant_id) {
            Some(value) => value,
            None => return tool_error("invalid_participant_id"),
        };
        if let Some(principal) = transport_principal {
            let principal = principal.clone();
            let lookup = participant_id.clone();
            let verify_channel = channel.clone();
            let allowed = match with_db(state, move |conn| {
                authorization::authorize(
                    conn,
                    &principal,
                    &lookup,
                    authorization::READ_MESSAGES,
                    Some(&verify_channel),
                )
            })
            .await
            {
                Ok(value) => value,
                Err(()) => return tool_error("database_unavailable"),
            };
            if !allowed {
                return tool_error("forbidden");
            }
        } else {
            let (_, proof) = match parse_auth(arguments) {
                Ok(value) => value,
                Err(code) => return tool_error(code),
            };
            let proof = proof.to_owned();
            let lookup = participant_id.clone();
            let verify_channel = channel.clone();
            let verified = match with_db(state, move |conn| {
                let Some(auth) = identity::get_web_participant_auth(conn, &lookup)? else {
                    return Ok(false);
                };
                Ok(auth.auth_scheme == participant_auth::AUTH_SCHEME
                    && participant_auth::verify_read_proof(
                        &auth.auth_secret,
                        &proof,
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

async fn blackboard_write(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
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
        transport_principal,
    )
    .await
    {
        Ok(writer) => writer,
        Err(code) => return tool_error(code),
    };

    let request_hash =
        execution::message_request_hash(&channel, &kind, &message_body, None, reply_to);
    let (principal, mechanism) = match transport_principal {
        Some(principal) => (principal.clone(), oidc::OIDC_MECHANISM.to_owned()),
        None => (
            execution::Principal {
                provider: "participant-hmac".to_owned(),
                subject: participant_id.clone(),
            },
            participant_auth::AUTH_SCHEME.to_owned(),
        ),
    };
    let authority = execution::AuthorityContext {
        principal: principal.clone(),
        mechanism,
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
        delivery_id: match transport_principal {
            Some(_) => format!("mcp-bearer:{participant_id}:{nonce}"),
            None => format!("mcp-hmac:{participant_id}:{nonce}"),
        },
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
            execution::MessageExecutionRequest {
                authority: &authority,
                ingress: &ingress,
                intent: &intent,
                channel: &write_channel,
                kind: &write_kind,
                body: &write_body,
                reply_to,
            },
        )
    })
    .await
    {
        Ok(result) => result,
        Err(()) => return tool_error("database_unavailable"),
    };

    let (status, persisted, idempotent) = match result {
        execution::MessageExecutionResult::Created(message) => ("created", message, false),
        execution::MessageExecutionResult::Existing(message) => ("existing", message, true),
        execution::MessageExecutionResult::IntentConflict => return tool_error("nonce_conflict"),
        execution::MessageExecutionResult::ReplyTargetNotFound => {
            return tool_error("reply_target_not_found")
        }
        execution::MessageExecutionResult::ChannelArchived => {
            return tool_error("channel_archived")
        }
        execution::MessageExecutionResult::AuthorizationDenied => return tool_error("forbidden"),
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
    transport_principal: Option<&execution::Principal>,
) -> Result<Identity, &'static str> {
    if transport_principal.is_some() {
        let lookup_participant = participant_id.to_owned();
        return match with_db(state, move |conn| {
            identity::get_web_participant(conn, &lookup_participant)
        })
        .await
        {
            Ok(Some(identity)) => Ok(identity),
            Ok(None) => Err("forbidden"),
            Err(()) => Err("database_unavailable"),
        };
    }
    let (_, proof) = parse_auth(arguments)?;
    let proof = proof.to_owned();
    let lookup_participant = participant_id.to_owned();
    let auth = match with_db(state, move |conn| {
        identity::get_web_participant_auth(conn, &lookup_participant)
    })
    .await
    {
        Ok(Some(auth)) => auth,
        Ok(None) => return Err("unauthorized"),
        Err(()) => return Err("database_unavailable"),
    };
    if auth.auth_scheme != participant_auth::AUTH_SCHEME {
        return Err("unsupported_auth_scheme");
    }
    if !participant_auth::verify_write_proof(
        &auth.auth_secret,
        &proof,
        participant_id,
        channel,
        kind,
        body,
        reply_to,
        nonce,
    ) {
        return Err("unauthorized");
    }
    Ok(auth.identity)
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
        "content": [{"type": "text", "text": text}],
        "isError": false
    })
}

fn tool_error(code: &'static str) -> Value {
    json!({
        "content": [{"type": "text", "text": code}],
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

fn resolve_http_bearer_principal(
    state: &McpHttpState,
    headers: &HeaderMap,
) -> Result<Option<execution::Principal>, ()> {
    let Some(value) = headers.get(header::AUTHORIZATION) else {
        return Ok(None);
    };
    let value = value.to_str().map_err(|_| ())?;
    let (scheme, token) = value.split_once(' ').ok_or(())?;
    if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() {
        return Err(());
    }
    let verifier = state.oidc_verifier.as_ref().ok_or(())?;
    verifier.verify(token).map(Some).map_err(|_| ())
}

fn bearer_unauthorized(id: Value, resource: Option<&ProtectedResourceConfig>) -> Response {
    let mut response = jsonrpc_http_error(StatusCode::UNAUTHORIZED, id, -32001, "unauthorized");
    let challenge = match resource {
        Some(resource) => format!("Bearer resource_metadata=\"{}\"", resource.metadata_url),
        None => "Bearer".to_owned(),
    };
    if let Ok(value) = HeaderValue::from_str(&challenge) {
        response
            .headers_mut()
            .insert(header::WWW_AUTHENTICATE, value);
    }
    response
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
        json!({"jsonrpc": "2.0", "id": id, "result": result}),
    )
}

fn jsonrpc_error_response(id: Value, code: i64, message: &'static str) -> Response {
    json_response(
        StatusCode::OK,
        json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}),
    )
}

fn jsonrpc_http_error(status: StatusCode, id: Value, code: i64, message: &'static str) -> Response {
    json_response(
        status,
        json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}),
    )
}

fn jsonrpc_http_error_data(
    status: StatusCode,
    id: Value,
    code: i64,
    message: &'static str,
    data: Value,
) -> Response {
    json_response(
        status,
        json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message, "data": data}}),
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
