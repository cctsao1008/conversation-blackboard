from pathlib import Path

mcp = Path("src/mcp.rs")
s = mcp.read_text(encoding="utf-8")

# ClientInfo is a 2026-era SHOULD: absent is valid; present-but-malformed is rejected.
old_client_info = '''    let client_info = meta
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
'''
new_client_info = '''    if let Some(client_info) = meta.get("io.modelcontextprotocol/clientInfo") {
        let client_info = client_info
            .as_object()
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
    }
'''
if old_client_info not in s:
    raise SystemExit("stdio clientInfo validation block not found")
s = s.replace(old_client_info, new_client_info, 1)

old_http_client_info = '''    let client_info = meta
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
'''
new_http_client_info = '''    if let Some(client_info) = meta.get("io.modelcontextprotocol/clientInfo") {
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
'''
if old_http_client_info not in s:
    raise SystemExit("HTTP clientInfo validation block not found")
s = s.replace(old_http_client_info, new_http_client_info, 1)

# Modern wire fields must not leak into the 2025-era legacy result shape.
s = s.replace(
'''fn tools_list_result() -> Value {
    json!({
        "resultType": "complete",
        "tools": [''',
'''fn tools_list_result() -> Value {
    json!({
        "tools": [''',
1,
)
old_tools_close = '''            }
        ],
        "ttlMs": 300000,
        "cacheScope": "public"
    })
}

fn read_output_schema()'''
new_tools_close = '''            }
        ]
    })
}

fn read_output_schema()'''
if old_tools_close not in s:
    raise SystemExit("modern fields in tools_list_result not found")
s = s.replace(old_tools_close, new_tools_close, 1)

s = s.replace(
'''    json!({
        "resultType": "complete",
        "structuredContent": structured_content,''',
'''    json!({
        "structuredContent": structured_content,''',
1,
)
s = s.replace(
'''    json!({
        "resultType": "complete",
        "content": [{"type": "text", "text": code}],
        "isError": true
    })''',
'''    json!({
        "content": [{"type": "text", "text": code}],
        "isError": true
    })''',
1,
)

# Adapt only the modern HTTP result on the wire, preserving the canonical tool implementation.
old_modern_return = '''        let result = match method {
            "server/discover" => discover_result(),
            "tools/list" => tools_list_result(),
            "tools/call" => match tool_call_result(&state, object).await {
                Ok(result) => result,
                Err(message) => return jsonrpc_error_response(id, -32602, message),
            },
            _ => return jsonrpc_http_error(StatusCode::NOT_FOUND, id, -32601, "method_not_found"),
        };
        return jsonrpc_result_response(id, result);
'''
new_modern_return = '''        let result = match method {
            "server/discover" => discover_result(),
            "tools/list" => modern_result(method, tools_list_result()),
            "tools/call" => match tool_call_result(&state, object).await {
                Ok(result) => modern_result(method, result),
                Err(message) => return jsonrpc_error_response(id, -32602, message),
            },
            _ => return jsonrpc_http_error(StatusCode::NOT_FOUND, id, -32601, "method_not_found"),
        };
        return jsonrpc_result_response(id, result);
'''
if old_modern_return not in s:
    raise SystemExit("modern result dispatch block not found")
s = s.replace(old_modern_return, new_modern_return, 1)

anchor = '''fn request_protocol_version(object: &Map<String, Value>) -> Option<&str> {'''
modern_result = '''fn modern_result(method: &str, mut result: Value) -> Value {
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

'''
if anchor not in s:
    raise SystemExit("modern result insertion anchor not found")
s = s.replace(anchor, modern_result + anchor, 1)

mcp.write_text(s, encoding="utf-8")

# Add compatibility regressions.
tests = Path("src/mcp_contract_tests.rs")
t = tests.read_text(encoding="utf-8")
append = r'''

#[tokio::test]
async fn modern_client_info_is_optional_but_malformed_client_info_is_rejected() {
    let fixture = fixture();
    let without_client_info = json!({
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}
    });
    let response = modern_post(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 94,
            "method": "server/discover",
            "params": {"_meta": without_client_info}
        }),
        "server/discover",
        None,
        "2026-07-28",
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);

    let malformed = modern_post(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 95,
            "method": "server/discover",
            "params": {"_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": "not-an-object"
            }}
        }),
        "server/discover",
        None,
        "2026-07-28",
    )
    .await;
    let (status, value) = response_json(malformed).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(value["error"]["code"], -32020);
}

#[tokio::test]
async fn legacy_result_wire_does_not_gain_modern_result_fields() {
    let fixture = fixture();
    let listed = request(
        &fixture.router,
        Method::POST,
        Some(json!({"jsonrpc": "2.0", "id": 96, "method": "tools/list", "params": {}})),
    )
    .await;
    let (status, value) = response_json(listed).await;
    assert_eq!(status, StatusCode::OK);
    assert!(value["result"].get("resultType").is_none());
    assert!(value["result"].get("ttlMs").is_none());
    assert!(value["result"].get("cacheScope").is_none());
    assert!(value["result"].get("_meta").is_none());
}

#[tokio::test]
async fn modern_tool_results_stamp_result_type_and_server_identity() {
    let fixture = fixture();
    let response = modern_post(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 97,
            "method": "tools/call",
            "params": {
                "name": "blackboard_read",
                "arguments": {"channel": "blackboard-lounge"},
                "_meta": modern_meta("2026-07-28")
            }
        }),
        "tools/call",
        Some("blackboard_read"),
        "2026-07-28",
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(value["result"]["resultType"], "complete");
    assert_eq!(
        value["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
        "conversation-blackboard"
    );
}
'''
if "legacy_result_wire_does_not_gain_modern_result_fields" in t:
    raise SystemExit("wire compatibility tests already present")
tests.write_text(t + append, encoding="utf-8")
