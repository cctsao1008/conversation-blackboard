from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")

s = replace_once(
    s,
    '        "tools/list" => Ok(tools_list_result()),\n',
    '        "tools/list" => Ok(tools_list_result()),\n',
    "stdio tools/list remains legacy projection",
)

s = replace_once(
    s,
    '            "tools/list" => modern_result(method, tools_list_result()),\n',
    '            "tools/list" => modern_result(\n                method,\n                tools_list_result_for_modern_http(state.oidc_verifier.is_some()),\n            ),\n',
    "modern tools/list projection",
)

anchor = '''fn tools_list_result() -> Value {\n'''
helpers = r'''fn tools_list_result_for_modern_http(bearer_capable: bool) -> Value {
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
'''
s = replace_once(s, anchor, helpers, "modern auth projection helper")
p.write_text(s, encoding="utf-8")

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
anchor = '''#[tokio::test]\nasync fn modern_tools_list_uses_per_request_metadata_and_cacheable_complete_result() {'''
tests = r'''fn tool_required_fields<'a>(value: &'a Value, name: &str) -> &'a Vec<Value> {
    value["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == name)
        .unwrap()["inputSchema"]["required"]
        .as_array()
        .unwrap()
}

#[tokio::test]
async fn modern_http_oidc_projection_makes_hmac_auth_optional_without_removing_fallback_schema() {
    let mut fixture = fixture();
    let (verifier, _) = bearer_verifier_and_token("remote-agent-1");
    fixture.router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );

    let response = modern_post(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 89,
            "method": "tools/list",
            "params": {"_meta": modern_meta("2026-07-28")}
        }),
        "tools/list",
        None,
        "2026-07-28",
    )
    .await;
    let (_, value) = response_json(response).await;
    for name in [
        "blackboard_write",
        "blackboard_access_context",
        "blackboard_execution_receipt",
        "blackboard_execution_audit",
        "blackboard_execution_audit_integrity",
        "blackboard_execution_audit_sweep",
    ] {
        let required = tool_required_fields(&value, name);
        assert!(!required.iter().any(|field| field == "auth"), "{name}");
        let tool = value["result"]["tools"]
            .as_array()
            .unwrap()
            .iter()
            .find(|tool| tool["name"] == name)
            .unwrap();
        assert!(tool["inputSchema"]["properties"]["auth"].is_object());
        assert!(tool["inputSchema"]["properties"]["auth"]["description"]
            .as_str()
            .unwrap()
            .contains("Bearer"));
    }
}

#[tokio::test]
async fn legacy_http_and_stdio_keep_hmac_auth_required_when_oidc_is_available() {
    let mut fixture = fixture();
    let (verifier, _) = bearer_verifier_and_token("remote-agent-1");
    fixture.router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );

    let legacy = request(
        &fixture.router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 88,
            "method": "tools/list",
            "params": {}
        })),
    )
    .await;
    let (_, legacy_value) = response_json(legacy).await;
    assert!(tool_required_fields(&legacy_value, "blackboard_write")
        .iter()
        .any(|field| field == "auth"));

    let stdio = mcp::stdio_dispatch(
        &AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        &json!({"jsonrpc": "2.0", "id": 87, "method": "tools/list", "params": {}}),
    )
    .await
    .unwrap();
    assert!(tool_required_fields(&stdio, "blackboard_write")
        .iter()
        .any(|field| field == "auth"));
}

#[tokio::test]
async fn modern_http_without_oidc_keeps_hmac_auth_required() {
    let fixture = fixture();
    let response = modern_post(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 86,
            "method": "tools/list",
            "params": {"_meta": modern_meta("2026-07-28")}
        }),
        "tools/list",
        None,
        "2026-07-28",
    )
    .await;
    let (_, value) = response_json(response).await;
    assert!(tool_required_fields(&value, "blackboard_write")
        .iter()
        .any(|field| field == "auth"));
}

''' + anchor
s = replace_once(s, anchor, tests, "modern auth projection tests")
p.write_text(s, encoding="utf-8")
