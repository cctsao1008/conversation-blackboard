from pathlib import Path
import re

mcp = Path("src/mcp.rs")
s = mcp.read_text(encoding="utf-8")

s = s.replace(
'''use std::sync::OnceLock;\n\nuse axum::{''',
'''use std::sync::OnceLock;\n\nuse base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};\nuse axum::{''',
1,
)

old_constants = '''const MAX_MCP_REQUEST_BYTES: usize = 128 * 1024;\nconst MAX_MESSAGE_BODY_BYTES: usize = 64 * 1024;\nconst MAX_PAGE_SIZE: usize = 200;\nconst DEFAULT_PAGE_SIZE: usize = 50;\nconst LATEST_PROTOCOL_VERSION: &str = "2025-11-25";\nconst SUPPORTED_PROTOCOL_VERSIONS: &[&str] = &["2025-03-26", "2025-06-18", "2025-11-25"];'''
new_constants = '''const MAX_MCP_REQUEST_BYTES: usize = 128 * 1024;\nconst MAX_MESSAGE_BODY_BYTES: usize = 64 * 1024;\nconst MAX_PAGE_SIZE: usize = 200;\nconst DEFAULT_PAGE_SIZE: usize = 50;\nconst MODERN_PROTOCOL_VERSION: &str = "2026-07-28";\nconst LATEST_LEGACY_PROTOCOL_VERSION: &str = "2025-11-25";\nconst SUPPORTED_LEGACY_PROTOCOL_VERSIONS: &[&str] = &["2025-03-26", "2025-06-18", "2025-11-25"];\nconst SUPPORTED_PROTOCOL_VERSIONS: &[&str] = &[\n    "2026-07-28",\n    "2025-11-25",\n    "2025-06-18",\n    "2025-03-26",\n];'''
if old_constants not in s:
    raise SystemExit("constants block not found")
s = s.replace(old_constants, new_constants, 1)

old_stdio = '''        "initialize" => match initialize_result(object) {\n            Ok(result) => Ok(result),\n            Err(message) => Err((-32602, message)),\n        },\n        "ping" => Ok(json!({})),\n        "tools/list" => Ok(tools_list_result()),'''
new_stdio = '''        "server/discover" => match validate_modern_request_metadata(object) {\n            Ok(()) => Ok(discover_result()),\n            Err(message) => Err((-32602, message)),\n        },\n        "initialize" => match initialize_result(object) {\n            Ok(result) => Ok(result),\n            Err(message) => Err((-32602, message)),\n        },\n        "ping" => Ok(json!({})),\n        "tools/list" => Ok(tools_list_result()),'''
if old_stdio not in s:
    raise SystemExit("stdio dispatch block not found")
s = s.replace(old_stdio, new_stdio, 1)

post_pattern = re.compile(r'async fn mcp_post\(State\(state\): State<AppState>, request: Request<Body>\) -> Response \{.*?\n\}\n\nfn initialize_result', re.S)
match = post_pattern.search(s)
if not match:
    raise SystemExit("mcp_post function not found")
new_post = r'''async fn mcp_post(State(state): State<AppState>, request: Request<Body>) -> Response {
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
        return jsonrpc_http_error(
            StatusCode::BAD_REQUEST,
            id,
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
                id,
                -32600,
                "invalid_request",
            )
        }
    };

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
            "tools/list" => tools_list_result(),
            "tools/call" => match tool_call_result(&state, object).await {
                Ok(result) => result,
                Err(message) => return jsonrpc_error_response(id, -32602, message),
            },
            _ => {
                return jsonrpc_http_error(
                    StatusCode::NOT_FOUND,
                    id,
                    -32601,
                    "method_not_found",
                )
            }
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
        "tools/call" => match tool_call_result(&state, object).await {
            Ok(result) => result,
            Err(message) => return jsonrpc_error_response(id, -32602, message),
        },
        _ => return jsonrpc_error_response(id, -32601, "method_not_found"),
    };

    jsonrpc_result_response(id, result)
}

fn initialize_result'''
s = s[:match.start()] + new_post + s[match.end():]

s = s.replace(
'''    let protocol_version = if SUPPORTED_PROTOCOL_VERSIONS.contains(&requested) {\n        requested\n    } else {\n        LATEST_PROTOCOL_VERSION\n    };''',
'''    let protocol_version = if SUPPORTED_LEGACY_PROTOCOL_VERSIONS.contains(&requested) {\n        requested\n    } else {\n        LATEST_LEGACY_PROTOCOL_VERSION\n    };''',
1,
)

anchor = '''fn auth_schema() -> Value {'''
if anchor not in s:
    raise SystemExit("auth_schema anchor missing")
modern_helpers = r'''fn discover_result() -> Value {
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
    let client_info = meta
        .get("io.modelcontextprotocol/clientInfo")
        .and_then(Value::as_object)
        .ok_or("invalid_request_metadata")?;
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
    let client_info = meta
        .get("io.modelcontextprotocol/clientInfo")
        .and_then(Value::as_object)
        .ok_or("header_mismatch")?;
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

'''
s = s.replace(anchor, modern_helpers + anchor, 1)

s = s.replace(
'''fn tools_list_result() -> Value {\n    json!({\n        "tools": [''',
'''fn tools_list_result() -> Value {\n    json!({\n        "resultType": "complete",\n        "tools": [''',
1,
)

# Add cache metadata to tools/list result just before its outer object closes.
needle = '''            }\n        ]\n    })\n}\n\nfn read_output_schema()'''
replacement = '''            }\n        ],\n        "ttlMs": 300000,\n        "cacheScope": "public"\n    })\n}\n\nfn read_output_schema()'''
if needle not in s:
    raise SystemExit("tools_list_result closing block not found")
s = s.replace(needle, replacement, 1)

s = s.replace(
'''    json!({\n        "structuredContent": structured_content,''',
'''    json!({\n        "resultType": "complete",\n        "structuredContent": structured_content,''',
1,
)
s = s.replace(
'''    json!({\n        "content": [{"type": "text", "text": code}],\n        "isError": true\n    })''',
'''    json!({\n        "resultType": "complete",\n        "content": [{"type": "text", "text": code}],\n        "isError": true\n    })''',
1,
)

# The old permissive protocol helper is replaced by the modern/legacy gate above.
s = re.sub(
    r'\nfn protocol_header_supported\(headers: &HeaderMap\) -> bool \{.*?\n\}\n\nfn origin_allowed',
    '\nfn origin_allowed',
    s,
    count=1,
    flags=re.S,
)

s = s.replace(
'''        HeaderValue::from_static("content-type, mcp-protocol-version, mcp-session-id"),''',
'''        HeaderValue::from_static(\n            "content-type, accept, mcp-protocol-version, mcp-method, mcp-name, mcp-session-id",\n        ),''',
1,
)

error_anchor = '''fn jsonrpc_http_error(status: StatusCode, id: Value, code: i64, message: &'static str) -> Response {\n    json_response(\n        status,\n        json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}),\n    )\n}\n'''
if error_anchor not in s:
    raise SystemExit("jsonrpc_http_error helper not found")
error_replacement = error_anchor + '''\nfn jsonrpc_http_error_data(\n    status: StatusCode,\n    id: Value,\n    code: i64,\n    message: &'static str,\n    data: Value,\n) -> Response {\n    json_response(\n        status,\n        json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message, "data": data}}),\n    )\n}\n'''
s = s.replace(error_anchor, error_replacement, 1)

mcp.write_text(s, encoding="utf-8")

# Add focused transport regressions without disturbing existing HMAC semantic tests.
tests = Path("src/mcp_contract_tests.rs")
t = tests.read_text(encoding="utf-8")
append = r'''

fn modern_meta(version: &str) -> Value {
    json!({
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "contract-test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {}
    })
}

async fn modern_post(
    router: &Router,
    body: Value,
    method_header: &str,
    name_header: Option<&str>,
    protocol_header: &str,
) -> Response {
    let mut builder = Request::builder()
        .method(Method::POST)
        .uri("/mcp")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, "application/json, text/event-stream")
        .header("mcp-protocol-version", protocol_header)
        .header("mcp-method", method_header);
    if let Some(name) = name_header {
        builder = builder.header("mcp-name", name);
    }
    router
        .clone()
        .oneshot(builder.body(Body::from(body.to_string())).unwrap())
        .await
        .unwrap()
}

#[tokio::test]
async fn modern_server_discover_is_stateless_and_advertises_current_version() {
    let fixture = fixture();
    let response = modern_post(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": "discover-1",
            "method": "server/discover",
            "params": {"_meta": modern_meta("2026-07-28")}
        }),
        "server/discover",
        None,
        "2026-07-28",
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    assert!(response.headers().get("mcp-session-id").is_none());
    let (_, value) = response_json(response).await;
    assert_eq!(value["result"]["resultType"], "complete");
    assert_eq!(
        value["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
        "conversation-blackboard"
    );
    let versions = value["result"]["supportedVersions"].as_array().unwrap();
    assert!(versions.iter().any(|version| version == "2026-07-28"));
    assert!(versions.iter().any(|version| version == "2025-11-25"));
}

#[tokio::test]
async fn modern_tools_list_uses_per_request_metadata_and_cacheable_complete_result() {
    let fixture = fixture();
    let response = modern_post(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 90,
            "method": "tools/list",
            "params": {"_meta": modern_meta("2026-07-28")}
        }),
        "tools/list",
        None,
        "2026-07-28",
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    assert!(response.headers().get("mcp-session-id").is_none());
    let (_, value) = response_json(response).await;
    assert_eq!(value["result"]["resultType"], "complete");
    assert_eq!(value["result"]["cacheScope"], "public");
    assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 7);
}

#[tokio::test]
async fn modern_streamable_http_rejects_header_body_mismatches() {
    let fixture = fixture();
    let body = json!({
        "jsonrpc": "2.0",
        "id": 91,
        "method": "tools/call",
        "params": {
            "name": "blackboard_read",
            "arguments": {"channel": "blackboard-lounge"},
            "_meta": modern_meta("2026-07-28")
        }
    });

    let method_mismatch = modern_post(
        &fixture.router,
        body.clone(),
        "tools/list",
        Some("blackboard_read"),
        "2026-07-28",
    )
    .await;
    let (status, value) = response_json(method_mismatch).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(value["error"]["code"], -32020);

    let name_mismatch = modern_post(
        &fixture.router,
        body.clone(),
        "tools/call",
        Some("blackboard_write"),
        "2026-07-28",
    )
    .await;
    let (status, value) = response_json(name_mismatch).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(value["error"]["code"], -32020);

    let protocol_mismatch = modern_post(
        &fixture.router,
        body,
        "tools/call",
        Some("blackboard_read"),
        "2025-11-25",
    )
    .await;
    let (status, value) = response_json(protocol_mismatch).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(value["error"]["code"], -32020);
}

#[tokio::test]
async fn modern_streamable_http_reports_unsupported_version_and_unknown_method() {
    let fixture = fixture();
    let unsupported = modern_post(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 92,
            "method": "tools/list",
            "params": {"_meta": modern_meta("2099-01-01")}
        }),
        "tools/list",
        None,
        "2099-01-01",
    )
    .await;
    let (status, value) = response_json(unsupported).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(value["error"]["code"], -32022);
    assert_eq!(value["error"]["data"]["requested"], "2099-01-01");
    assert!(value["error"]["data"]["supported"]
        .as_array()
        .unwrap()
        .iter()
        .any(|version| version == "2026-07-28"));

    let unknown = modern_post(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 93,
            "method": "unknown/method",
            "params": {"_meta": modern_meta("2026-07-28")}
        }),
        "unknown/method",
        None,
        "2026-07-28",
    )
    .await;
    let (status, value) = response_json(unknown).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(value["error"]["code"], -32601);
}
'''
if "modern_server_discover_is_stateless_and_advertises_current_version" in t:
    raise SystemExit("modern tests already present")
tests.write_text(t + append, encoding="utf-8")
