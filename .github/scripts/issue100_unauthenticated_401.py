from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")
anchor = '''    let transport_principal = match resolve_http_bearer_principal(&state, &headers) {\n        Ok(principal) => principal,\n        Err(()) => return bearer_unauthorized(id, state.protected_resource.as_ref()),\n    };\n\n'''
replacement = anchor + '''    if state.oidc_verifier.is_some()\n        && transport_principal.is_none()\n        && http_tool_call_requires_auth(&state.app, object).await\n    {\n        return bearer_unauthorized(id, state.protected_resource.as_ref());\n    }\n\n'''
s = replace_once(s, anchor, replacement, "HTTP protected tool authentication gate")

anchor = '''fn request_protocol_version(object: &Map<String, Value>) -> Option<&str> {\n'''
helper = r'''async fn http_tool_call_requires_auth(state: &AppState, object: &Map<String, Value>) -> bool {
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
        | "blackboard_execution_audit_sweep" => true,
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
            match with_db(state, move |conn| db::channel_is_public_active(conn, &channel)).await {
                Ok(public) => !public,
                Err(()) => false,
            }
        }
        _ => false,
    }
}

fn request_protocol_version(object: &Map<String, Value>) -> Option<&str> {
'''
s = replace_once(s, anchor, helper, "protected tool auth classifier")
p.write_text(s, encoding="utf-8")

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
anchor = '''#[tokio::test]\nasync fn expired_bearer_is_rejected_at_mcp_transport_gate() {'''
tests = r'''#[tokio::test]
async fn oidc_enabled_remote_mcp_returns_401_for_protected_tool_without_any_credential() {
    let fixture = fixture();
    let (verifier, _) = bearer_verifier_and_token("remote-agent-1");
    let router = mcp::app_with_remote_auth(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
        Some("https://board.example/mcp"),
    )
    .unwrap();
    let response = request(
        &router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 108,
            "method": "tools/call",
            "params": {
                "name": "blackboard_write",
                "arguments": {
                    "participant_id": "single-main",
                    "channel": "control-systems",
                    "body": "must-not-land",
                    "nonce": "issue100-no-credential"
                }
            }
        })),
    )
    .await;
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(
        response.headers().get(header::WWW_AUTHENTICATE).unwrap(),
        "Bearer resource_metadata=\"https://board.example/.well-known/oauth-protected-resource/mcp\""
    );
}

#[tokio::test]
async fn oidc_enabled_remote_mcp_keeps_public_read_anonymous_and_challenges_private_read() {
    let fixture = fixture();
    let (verifier, _) = bearer_verifier_and_token("remote-agent-1");
    let router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );

    // Materialize the lounge through the normal authenticated write path before
    // asserting anonymous visibility. An absent channel is not an active public
    // resource and must not be treated as public merely by name.
    let seeded = call_tool(
        &router,
        1089,
        "blackboard_write",
        write_arguments(
            &fixture.single_secret,
            "single-main",
            "blackboard-lounge",
            "message",
            "issue100-public-seed",
            None,
            "issue100-public-seed",
        ),
    )
    .await;
    assert_eq!(seeded["result"]["isError"], false);

    let public_response = request(
        &router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 109,
            "method": "tools/call",
            "params": {
                "name": "blackboard_read",
                "arguments": {"channel": "blackboard-lounge", "after": 0, "limit": 50}
            }
        })),
    )
    .await;
    assert_eq!(public_response.status(), StatusCode::OK);

    let private_response = request(
        &router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 110,
            "method": "tools/call",
            "params": {
                "name": "blackboard_read",
                "arguments": {"channel": "control-systems", "after": 0, "limit": 50}
            }
        })),
    )
    .await;
    assert_eq!(private_response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(
        private_response.headers().get(header::WWW_AUTHENTICATE).unwrap(),
        "Bearer"
    );
}

#[tokio::test]
async fn oidc_enabled_remote_mcp_still_accepts_no_bearer_valid_hmac_fallback() {
    let mut fixture = fixture();
    let (verifier, _) = bearer_verifier_and_token("remote-agent-1");
    fixture.router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );
    let value = call_tool(
        &fixture.router,
        111,
        "blackboard_write",
        write_arguments(
            &fixture.single_secret,
            "single-main",
            "blackboard-lounge",
            "message",
            "hmac-fallback-still-works",
            None,
            "issue100-hmac-fallback",
        ),
    )
    .await;
    assert_eq!(value["result"]["isError"], false);
    assert_eq!(value["result"]["structuredContent"]["status"], "created");
}

''' + anchor
s = replace_once(s, anchor, tests, "protected 401 contract tests")
p.write_text(s, encoding="utf-8")
