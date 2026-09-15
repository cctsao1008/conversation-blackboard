from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# 1) Dedicated HMAC canonicalization that binds the complete decision query.
p = Path("src/participant_auth.rs")
s = p.read_text(encoding="utf-8")
anchor = '''pub fn canonical_capability_bytes(\n    participant_id: &str,\n    capability: &str,\n    resource: Option<&str>,\n) -> Vec<u8> {'''
insert = r'''#[allow(clippy::too_many_arguments)]
pub fn canonical_authorization_decision_bytes(
    participant_id: &str,
    principal_provider: &str,
    principal_subject: &str,
    target_participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: Option<&str>,
) -> Vec<u8> {
    let mut payload = BTreeMap::<String, Value>::new();
    payload.insert("auth_version".into(), json!(AUTH_SCHEME));
    payload.insert("capability".into(), json!(capability));
    payload.insert("intent_id".into(), json!(intent_id));
    payload.insert("participant_id".into(), json!(participant_id));
    payload.insert("principal_provider".into(), json!(principal_provider));
    payload.insert("principal_subject".into(), json!(principal_subject));
    payload.insert("purpose".into(), json!("authorization-decision-explain-v1"));
    payload.insert("resource".into(), json!(resource));
    payload.insert("target_participant_id".into(), json!(target_participant_id));
    serde_json::to_vec(&payload).expect("canonical authorization decision payload must serialize")
}

#[cfg(test)]
#[allow(clippy::too_many_arguments)]
pub fn compute_authorization_decision_proof(
    secret: &str,
    participant_id: &str,
    principal_provider: &str,
    principal_subject: &str,
    target_participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: Option<&str>,
) -> Option<String> {
    let canonical = canonical_authorization_decision_bytes(
        participant_id,
        principal_provider,
        principal_subject,
        target_participant_id,
        capability,
        resource,
        intent_id,
    );
    compute_message_proof(secret, &canonical)
}

'''
if "canonical_authorization_decision_bytes" in s:
    raise SystemExit("decision HMAC canonicalization already exists")
s = replace_once(s, anchor, insert + anchor, "participant auth canonical capability anchor")
p.write_text(s, encoding="utf-8")

# 2) Canonical external decision output schema.
p = Path("src/contract_schema.rs")
s = p.read_text(encoding="utf-8")
anchor = '''pub fn authorization_integrity_violation_schema() -> Value {'''
insert = r'''pub fn authorization_decision_explanation_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "allowed": {"type": "boolean"},
            "source": {"type": "string"},
            "reason": {"type": "string"},
            "grant_id": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "consume_on_commit": {"type": "boolean"}
        },
        "required": ["allowed", "source", "reason", "grant_id", "consume_on_commit"],
        "additionalProperties": false,
        "description": "Canonical non-secret read-only explanation of one authorization decision."
    })
}

pub fn authorization_decision_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"decision": authorization_decision_explanation_schema()},
        "required": ["decision"],
        "additionalProperties": false
    })
}

'''
if "authorization_decision_explanation_schema" in s:
    raise SystemExit("decision contract schema already exists")
s = replace_once(s, anchor, insert + anchor, "contract decision schema anchor")
p.write_text(s, encoding="utf-8")

# 3) MCP tool projection.
p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")

old = '''        | "blackboard_authorization_policy_integrity"\n        | "blackboard_authorization_policy" => true,'''
new = '''        | "blackboard_authorization_policy_integrity"\n        | "blackboard_authorization_policy"\n        | "blackboard_authorization_decision" => true,'''
s = replace_once(s, old, new, "protected MCP tool list")

# Add tool descriptor immediately after policy snapshot descriptor.
old = '''            {\n                "name": "blackboard_authorization_policy",\n                "title": "Read Blackboard Authorization Policy",\n                "description": "Read the privileged canonical inventory of explicit durable and delegated authorization objects without modifying, normalizing, repairing, or consuming authority.",\n                "inputSchema": {\n                    "type": "object",\n                    "properties": {\n                        "participant_id": contract_schema::participant_id_schema(),\n                        "auth": auth_schema()\n                    },\n                    "required": ["participant_id", "auth"],\n                    "additionalProperties": false\n                },\n                "outputSchema": contract_schema::authorization_policy_envelope_schema(),\n                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}\n            }\n        ]'''
new = '''            {\n                "name": "blackboard_authorization_policy",\n                "title": "Read Blackboard Authorization Policy",\n                "description": "Read the privileged canonical inventory of explicit durable and delegated authorization objects without modifying, normalizing, repairing, or consuming authority.",\n                "inputSchema": {\n                    "type": "object",\n                    "properties": {\n                        "participant_id": contract_schema::participant_id_schema(),\n                        "auth": auth_schema()\n                    },\n                    "required": ["participant_id", "auth"],\n                    "additionalProperties": false\n                },\n                "outputSchema": contract_schema::authorization_policy_envelope_schema(),\n                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}\n            },\n            {\n                "name": "blackboard_authorization_decision",\n                "title": "Explain Blackboard Authorization Decision",\n                "description": "Explain one target authorization decision using the canonical read-only policy evaluator. Caller authority is separate from the target principal. Participant-HMAC auth for this tool is bound to the complete target decision query.",\n                "inputSchema": {\n                    "type": "object",\n                    "properties": {\n                        "participant_id": contract_schema::participant_id_schema(),\n                        "auth": auth_schema(),\n                        "principal_provider": {"type": "string", "minLength": 1, "maxLength": 256},\n                        "principal_subject": {"type": "string", "minLength": 1, "maxLength": 256},\n                        "target_participant_id": contract_schema::participant_id_schema(),\n                        "capability": {"type": "string", "minLength": 1, "maxLength": 128},\n                        "resource": {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 256}, {"type": "null"}]},\n                        "intent_id": {"anyOf": [contract_schema::intent_id_schema(), {"type": "null"}]}\n                    },\n                    "required": ["participant_id", "auth", "principal_provider", "principal_subject", "target_participant_id", "capability"],\n                    "additionalProperties": false\n                },\n                "outputSchema": contract_schema::authorization_decision_envelope_schema(),\n                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}\n            }\n        ]'''
s = replace_once(s, old, new, "MCP policy tool descriptor")

old = '''        "blackboard_authorization_policy" => {\n            Ok(blackboard_authorization_policy(state, &arguments, transport_principal).await)\n        }\n        _ => Err("unknown_tool"),'''
new = '''        "blackboard_authorization_policy" => {\n            Ok(blackboard_authorization_policy(state, &arguments, transport_principal).await)\n        }\n        "blackboard_authorization_decision" => {\n            Ok(blackboard_authorization_decision(state, &arguments, transport_principal).await)\n        }\n        _ => Err("unknown_tool"),'''
s = replace_once(s, old, new, "MCP tool dispatch")

# Insert decision function before policy snapshot implementation.
anchor = '''async fn blackboard_authorization_policy(\n'''
handler = r'''async fn blackboard_authorization_decision(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(
        arguments,
        &[
            "participant_id",
            "auth",
            "principal_provider",
            "principal_subject",
            "target_participant_id",
            "capability",
            "resource",
            "intent_id",
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
    let principal_provider = match arguments.get("principal_provider").and_then(Value::as_str) {
        Some(value) => value,
        None => return tool_error("invalid_principal_provider"),
    };
    let principal_subject = match arguments.get("principal_subject").and_then(Value::as_str) {
        Some(value) => value,
        None => return tool_error("invalid_principal_subject"),
    };
    let target_participant_id =
        match arguments.get("target_participant_id").and_then(Value::as_str) {
            Some(value) => value,
            None => return tool_error("invalid_participant_id"),
        };
    let capability = match arguments.get("capability").and_then(Value::as_str) {
        Some(value) => value,
        None => return tool_error("invalid_capability"),
    };
    let resource = match arguments.get("resource") {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) => Some(value.as_str()),
        Some(_) => return tool_error("invalid_resource"),
    };
    let intent_id = match arguments.get("intent_id") {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) => Some(value.as_str()),
        Some(_) => return tool_error("invalid_intent_id"),
    };
    let target = match authorization::normalize_authorization_decision_target(
        principal_provider,
        principal_subject,
        target_participant_id,
        capability,
        resource,
        intent_id,
    ) {
        Ok(target) => target,
        Err(code) => return tool_error(code),
    };

    let caller_principal = if let Some(principal) = transport_principal {
        principal.clone()
    } else {
        let (_, proof) = match parse_auth(arguments) {
            Ok(value) => value,
            Err(code) => return tool_error(code),
        };
        let proof = proof.to_owned();
        let lookup = participant_id.clone();
        let auth = match with_db_read_only(state, move |conn| {
            identity::get_web_participant_auth(conn, &lookup)
        })
        .await
        {
            Ok(Some(auth)) => auth,
            Ok(None) => return tool_error("unauthorized"),
            Err(()) => return tool_error("database_unavailable"),
        };
        if auth.auth_scheme != participant_auth::AUTH_SCHEME {
            return tool_error("unsupported_auth_scheme");
        }
        let canonical = participant_auth::canonical_authorization_decision_bytes(
            &participant_id,
            &target.principal.provider,
            &target.principal.subject,
            &target.participant_id,
            &target.capability,
            target.resource.as_deref(),
            target.intent_id.as_deref(),
        );
        if !participant_auth::verify_message_proof(&auth.auth_secret, &proof, &canonical) {
            return tool_error("unauthorized");
        }
        execution::Principal {
            provider: "participant-hmac".to_owned(),
            subject: participant_id.clone(),
        }
    };

    let policy_principal = caller_principal.clone();
    let caller_participant = participant_id.clone();
    let (schema_current, caller_allowed, decision) = match with_db_read_only(state, move |conn| {
        if !authorization::authorization_decision_schema_current(conn)? {
            return Ok((false, false, None));
        }
        let caller_decision = authorization::explain_authorization(
            conn,
            &policy_principal,
            &caller_participant,
            authorization::READ_AUTHORIZATION_DECISION,
            Some(authorization::AUTHORIZATION_DECISION_RESOURCE),
            None,
        )?;
        if !caller_decision.allowed {
            return Ok((true, false, None));
        }
        let decision = authorization::explain_authorization(
            conn,
            &target.principal,
            &target.participant_id,
            &target.capability,
            target.resource.as_deref(),
            target.intent_id.as_deref(),
        )?;
        Ok((
            true,
            true,
            Some(authorization::AuthorizationDecisionExplanation::from(decision)),
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
    if !caller_allowed {
        return tool_error("forbidden");
    }
    tool_success(json!({
        "decision": decision.expect("authorized decision explanation must produce a decision")
    }))
}

'''
if "async fn blackboard_authorization_decision(" in s:
    raise SystemExit("MCP decision handler already exists")
s = replace_once(s, anchor, handler + anchor, "MCP decision handler anchor")

# Read-only connection helper for observational decision path.
anchor = '''fn tool_success(structured_content: Value) -> Value {'''
helper = r'''async fn with_db_read_only<T, F>(state: &AppState, operation: F) -> Result<T, ()>
where
    T: Send + 'static,
    F: FnOnce(&rusqlite::Connection) -> rusqlite::Result<T> + Send + 'static,
{
    let db_path = state.db_path.clone();
    tokio::task::spawn_blocking(move || {
        let conn = db::connect_read_only(&db_path).map_err(|_| ())?;
        operation(&conn).map_err(|_| ())
    })
    .await
    .map_err(|_| ())?
}

'''
if "async fn with_db_read_only" in s:
    raise SystemExit("MCP read-only helper already exists")
s = replace_once(s, anchor, helper + anchor, "MCP DB helper anchor")
p.write_text(s, encoding="utf-8")

# 4) MCP regressions: schema, HMAC query binding, Bearer authority, no migration.
p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")

anchor = '''fn policy_integrity_arguments(secret: &str, participant_id: &str) -> Value {'''
helper = r'''#[allow(clippy::too_many_arguments)]
fn decision_arguments(
    secret: &str,
    participant_id: &str,
    principal_provider: &str,
    principal_subject: &str,
    target_participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: Option<&str>,
) -> Value {
    let proof = participant_auth::compute_authorization_decision_proof(
        secret,
        participant_id,
        principal_provider,
        principal_subject,
        target_participant_id,
        capability,
        resource,
        intent_id,
    )
    .unwrap();
    let mut value = json!({
        "participant_id": participant_id,
        "principal_provider": principal_provider,
        "principal_subject": principal_subject,
        "target_participant_id": target_participant_id,
        "capability": capability,
        "auth": {"scheme": participant_auth::AUTH_SCHEME, "proof": proof}
    });
    if let Some(resource) = resource {
        value["resource"] = json!(resource);
    }
    if let Some(intent_id) = intent_id {
        value["intent_id"] = json!(intent_id);
    }
    value
}

'''
if "fn decision_arguments(" in s:
    raise SystemExit("decision MCP test helper already exists")
s = replace_once(s, anchor, helper + anchor, "MCP test helper anchor")

# Insert tests before audit-sweep tests.
anchor = '''#[tokio::test]\nasync fn mcp_audit_sweep_is_privileged_and_projects_canonical_report() {'''
tests = r'''#[tokio::test]
async fn mcp_authorization_decision_tool_is_read_only_and_binds_hmac_to_target_query() {
    let fixture = fixture();

    let listed = mcp::stdio_dispatch(
        &AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        &json!({"jsonrpc": "2.0", "id": 131, "method": "tools/list", "params": {}}),
    )
    .await
    .unwrap();
    let tool = listed["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "blackboard_authorization_decision")
        .expect("authorization decision tool must be advertised");
    assert_eq!(
        tool["outputSchema"],
        contract_schema::authorization_decision_envelope_schema()
    );
    assert_eq!(tool["annotations"]["readOnlyHint"], true);
    assert!(tool["inputSchema"]["required"]
        .as_array()
        .unwrap()
        .iter()
        .any(|field| field == "auth"));

    let args = decision_arguments(
        &fixture.single_secret,
        "single-main",
        "oidc:https://issuer.example",
        "target-agent",
        "rotary-main",
        authorization::POST_MESSAGE,
        Some("alpha"),
        None,
    );
    let denied = call_tool(
        &fixture.router,
        132,
        "blackboard_authorization_decision",
        args.clone(),
    )
    .await;
    assert_eq!(tool_error_code(&denied), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main',
                 'read_authorization_decision', 'authorization-decision')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('oidc:https://issuer.example', 'target-agent', 'rotary-main',
                 'post_message', 'alpha')",
        [],
    )
    .unwrap();
    drop(conn);

    let allowed = call_tool(
        &fixture.router,
        133,
        "blackboard_authorization_decision",
        args.clone(),
    )
    .await;
    assert_eq!(allowed["result"]["isError"], false);
    assert_eq!(
        allowed["result"]["structuredContent"]["decision"]["allowed"],
        true
    );
    assert_eq!(
        allowed["result"]["structuredContent"]["decision"]["reason"],
        "explicit_durable_grant_match"
    );

    // Reusing the exact proof after changing one target field must fail authentication.
    let mut changed = args.clone();
    changed["resource"] = json!("beta");
    let replay = call_tool(
        &fixture.router,
        134,
        "blackboard_authorization_decision",
        changed,
    )
    .await;
    assert_eq!(tool_error_code(&replay), "unauthorized");

    // A freshly authenticated target mismatch is successful explanation data.
    let mismatch = call_tool(
        &fixture.router,
        135,
        "blackboard_authorization_decision",
        decision_arguments(
            &fixture.single_secret,
            "single-main",
            "oidc:https://issuer.example",
            "target-agent",
            "rotary-main",
            authorization::POST_MESSAGE,
            Some("beta"),
            None,
        ),
    )
    .await;
    assert_eq!(mismatch["result"]["isError"], false);
    assert_eq!(
        mismatch["result"]["structuredContent"]["decision"]["allowed"],
        false
    );
    assert_eq!(
        mismatch["result"]["structuredContent"]["decision"]["reason"],
        "explicit_resource_scope_mismatch"
    );

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    drop(conn);
    let legacy = call_tool(
        &fixture.router,
        136,
        "blackboard_authorization_decision",
        decision_arguments(
            &fixture.single_secret,
            "single-main",
            "oidc:https://issuer.example",
            "target-agent",
            "rotary-main",
            authorization::POST_MESSAGE,
            Some("alpha"),
            None,
        ),
    )
    .await;
    assert_eq!(tool_error_code(&legacy), "authorization_schema_not_current");
    let conn = db::connect(&fixture.db_path).unwrap();
    let table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'delegated_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(table_count, 0, "MCP decision explain recreated authorization schema");
}

#[tokio::test]
async fn bearer_authorization_decision_requires_explicit_explain_grant_and_omits_hmac_auth() {
    let fixture = fixture();
    let (verifier, token) = bearer_verifier_and_token("decision-reader");
    let router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );
    let authorization_header = format!("Bearer {token}");

    let denied = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 137,
            "method": "tools/call",
            "params": {
                "name": "blackboard_authorization_decision",
                "arguments": {
                    "participant_id": "single-main",
                    "principal_provider": "participant-hmac",
                    "principal_subject": "rotary-main",
                    "target_participant_id": "rotary-main",
                    "capability": "read_messages",
                    "resource": "blackboard-lounge"
                }
            }
        }),
        &authorization_header,
    )
    .await;
    let (status, denied_value) = response_json(denied).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(tool_error_code(&denied_value), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('oidc:https://issuer.example', 'decision-reader', 'single-main',
                 'read_authorization_decision', 'authorization-decision')",
        [],
    )
    .unwrap();
    drop(conn);

    let allowed = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 138,
            "method": "tools/call",
            "params": {
                "name": "blackboard_authorization_decision",
                "arguments": {
                    "participant_id": "single-main",
                    "principal_provider": "participant-hmac",
                    "principal_subject": "rotary-main",
                    "target_participant_id": "rotary-main",
                    "capability": "read_messages",
                    "resource": "blackboard-lounge"
                }
            }
        }),
        &authorization_header,
    )
    .await;
    let (status, allowed_value) = response_json(allowed).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(allowed_value["result"]["isError"], false);
    assert_eq!(
        allowed_value["result"]["structuredContent"]["decision"]["allowed"],
        true
    );
    assert_eq!(
        allowed_value["result"]["structuredContent"]["decision"]["source"],
        "implicit_authority"
    );

    // Modern HTTP OIDC discovery projects auth as optional for this tool.
    let modern_list = Request::builder()
        .method(Method::POST)
        .uri("/mcp")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, "application/json, text/event-stream")
        .header("mcp-protocol-version", "2026-07-28")
        .header("mcp-method", "tools/list")
        .body(Body::from(
            json!({
                "jsonrpc": "2.0",
                "id": 139,
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {}
                    }
                }
            })
            .to_string(),
        ))
        .unwrap();
    let modern_response = router.clone().oneshot(modern_list).await.unwrap();
    let (status, modern_value) = response_json(modern_response).await;
    assert_eq!(status, StatusCode::OK);
    let tool = modern_value["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "blackboard_authorization_decision")
        .unwrap();
    assert!(!tool["inputSchema"]["required"]
        .as_array()
        .unwrap()
        .iter()
        .any(|field| field == "auth"));
    assert!(tool["inputSchema"]["properties"].get("auth").is_some());
}

'''
if "mcp_authorization_decision_tool_is_read_only_and_binds_hmac_to_target_query" in s:
    raise SystemExit("MCP decision regressions already exist")
s = replace_once(s, anchor, tests + anchor, "MCP decision test anchor")
p.write_text(s, encoding="utf-8")
