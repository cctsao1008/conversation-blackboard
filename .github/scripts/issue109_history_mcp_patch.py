from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

# Canonical external schema
schema_path = Path("src/contract_schema.rs")
schema = schema_path.read_text()
insert = r'''
pub fn authorization_administration_event_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "id": {"type": "integer", "minimum": 1},
            "grant_store": {"type": "string", "enum": ["durable", "delegated"]},
            "grant_id": {"type": "integer", "minimum": 1},
            "operation": {"type": "string", "enum": ["create", "reactivate", "deactivate"]},
            "actor_surface": {"type": "string"},
            "actor_provider": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "actor_subject": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "actor_participant_id": {"anyOf": [participant_id_schema(), {"type": "null"}]},
            "target_principal_provider": {"type": "string"},
            "target_principal_subject": {"type": "string"},
            "participant_id": participant_id_schema(),
            "capability": {"type": "string"},
            "resource": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "intent_id": {"anyOf": [intent_id_schema(), {"type": "null"}]},
            "expires_at": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "one_shot": {"type": "boolean"},
            "before_status": {"anyOf": [{"type": "string", "enum": ["active", "inactive"]}, {"type": "null"}]},
            "after_status": {"type": "string", "enum": ["active", "inactive"]},
            "created_at": {"type": "integer"}
        },
        "required": [
            "id", "grant_store", "grant_id", "operation", "actor_surface",
            "actor_provider", "actor_subject", "actor_participant_id",
            "target_principal_provider", "target_principal_subject", "participant_id",
            "capability", "resource", "intent_id", "expires_at", "one_shot",
            "before_status", "after_status", "created_at"
        ],
        "additionalProperties": false,
        "description": "Immutable non-secret authorization-administration provenance for one effective policy mutation."
    })
}

pub fn authorization_administration_history_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "events": {"type": "array", "items": authorization_administration_event_schema()}
        },
        "required": ["events"],
        "additionalProperties": false,
        "description": "Canonical read-only committed authorization-administration history."
    })
}

pub fn authorization_administration_history_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"history": authorization_administration_history_schema()},
        "required": ["history"],
        "additionalProperties": false
    })
}

'''
schema = replace_once(
    schema,
    'pub fn authorization_decision_explanation_schema() -> Value {',
    insert + 'pub fn authorization_decision_explanation_schema() -> Value {',
    "history contract schema",
)
schema_path.write_text(schema)

# MCP runtime
mcp_path = Path("src/mcp.rs")
mcp = mcp_path.read_text()
mcp = replace_once(
    mcp,
    '    adapter_profile, authorization, contract_schema, db, execution, http::AppState, identity,\n    model::Identity, oidc, participant_auth,\n};',
    '    adapter_profile, authorization, authorization_admin, contract_schema, db, execution,\n    http::AppState, identity, model::Identity, oidc, participant_auth,\n};',
    "mcp authorization_admin import",
)
mcp = replace_once(
    mcp,
    '        | "blackboard_authorization_policy"\n        | "blackboard_authorization_decision" => true,',
    '        | "blackboard_authorization_policy"\n        | "blackboard_authorization_decision"\n        | "blackboard_authorization_administration_history" => true,',
    "protected tool classifier",
)
new_tool = r'''            {
                "name": "blackboard_authorization_administration_history",
                "title": "Read Blackboard Authorization Administration History",
                "description": "Read immutable privileged authorization-administration provenance without modifying, repairing, or re-evaluating policy. Optional filter_participant_id only filters returned history and does not scope reader authority.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "filter_participant_id": {"anyOf": [contract_schema::participant_id_schema(), {"type": "null"}]},
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::authorization_administration_history_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            },
'''
mcp = replace_once(
    mcp,
    '            {\n                "name": "blackboard_authorization_decision",',
    new_tool + '            {\n                "name": "blackboard_authorization_decision",',
    "history tool registry",
)
mcp = replace_once(
    mcp,
    '        "blackboard_authorization_policy" => {\n            Ok(blackboard_authorization_policy(state, &arguments, transport_principal).await)\n        }\n        "blackboard_authorization_decision" => {',
    '        "blackboard_authorization_policy" => {\n            Ok(blackboard_authorization_policy(state, &arguments, transport_principal).await)\n        }\n        "blackboard_authorization_administration_history" => Ok(\n            blackboard_authorization_administration_history(\n                state,\n                &arguments,\n                transport_principal,\n            )\n            .await,\n        ),\n        "blackboard_authorization_decision" => {',
    "history dispatch",
)
handler = r'''
async fn blackboard_authorization_administration_history(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(
        arguments,
        &["participant_id", "filter_participant_id", "auth"],
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
    let filter_participant_id = match arguments.get("filter_participant_id") {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == *value => Some(normalized),
            _ => return tool_error("invalid_filter_participant_id"),
        },
        Some(_) => return tool_error("invalid_filter_participant_id"),
    };

    let caller_principal = if let Some(principal) = transport_principal {
        principal.clone()
    } else {
        if let Err(code) = resolve_capability_identity(
            state,
            arguments,
            &participant_id,
            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
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

    let policy_principal = caller_principal.clone();
    let caller_participant = participant_id.clone();
    let filter = filter_participant_id.clone();
    let (schema_current, allowed, events) = match with_db_read_only(state, move |conn| {
        if !authorization_admin::schema_current(conn)? {
            return Ok((false, false, Vec::new()));
        }
        let decision = authorization::explain_authorization(
            conn,
            &policy_principal,
            &caller_participant,
            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
            None,
        )?;
        if !decision.allowed {
            return Ok((true, false, Vec::new()));
        }
        Ok((
            true,
            true,
            authorization_admin::read_administration_events(conn, filter.as_deref())?,
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
    tool_success(json!({"history": {"events": events}}))
}

'''
mcp = replace_once(
    mcp,
    'async fn blackboard_authorization_policy(\n',
    handler + 'async fn blackboard_authorization_policy(\n',
    "history handler",
)
mcp_path.write_text(mcp)

# MCP contract tests + inventory count
ct_path = Path("src/mcp_contract_tests.rs")
ct = ct_path.read_text()
ct = replace_once(
    ct,
    '    authorization, contract_schema, db, http::AppState, identity, mcp, oidc, participant_auth,\n};',
    '    authorization, authorization_admin, contract_schema, db, http::AppState, identity, mcp,\n    oidc, participant_auth,\n};',
    "mcp test import",
)
ct = ct.replace('assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 10);', 'assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 11);')
ct = ct.replace('assert_eq!(tools.len(), 10);', 'assert_eq!(tools.len(), 11);')
append = r'''

fn administration_history_arguments(secret: &str, participant_id: &str, filter: Option<&str>) -> Value {
    let proof = participant_auth::compute_capability_proof(
        secret,
        participant_id,
        authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
        Some(authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
    )
    .unwrap();
    let mut value = json!({
        "participant_id": participant_id,
        "auth": {"scheme": participant_auth::AUTH_SCHEME, "proof": proof}
    });
    if let Some(filter) = filter {
        value["filter_participant_id"] = json!(filter);
    }
    value
}

#[tokio::test]
async fn mcp_authorization_administration_history_requires_explicit_hmac_authority_and_filters() {
    let fixture = fixture();
    let conn = db::connect(&fixture.db_path).unwrap();
    let actor = authorization_admin::AuthorizationAdministrationActor::local_cli();
    let event_request = authorization_admin::DurableGrantCreateRequest {
        principal_provider: "oidc:https://issuer.example",
        principal_subject: "history-target",
        participant_id: "rotary-main",
        capability: authorization::READ_MESSAGES,
        resource: None,
    };
    let event_grant = authorization_admin::create_durable_grant(&conn, &actor, &event_request).unwrap();
    drop(conn);

    let denied = call_tool(
        &fixture.router,
        901,
        "blackboard_authorization_administration_history",
        administration_history_arguments(&fixture.single_secret, "single-main", Some("rotary-main")),
    )
    .await;
    assert_eq!(tool_error_code(&denied), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main', ?1, ?2)",
        params![
            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE,
        ],
    )
    .unwrap();
    drop(conn);

    let allowed = call_tool(
        &fixture.router,
        902,
        "blackboard_authorization_administration_history",
        administration_history_arguments(&fixture.single_secret, "single-main", Some("rotary-main")),
    )
    .await;
    assert_eq!(allowed["result"]["isError"], false);
    let events = allowed["result"]["structuredContent"]["history"]["events"]
        .as_array()
        .unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["grant_id"], event_grant.id);
    assert_eq!(events[0]["participant_id"], "rotary-main");
}

#[tokio::test]
async fn mcp_authorization_administration_history_bearer_requires_explicit_grant() {
    let mut fixture = fixture();
    let conn = db::connect(&fixture.db_path).unwrap();
    let actor = authorization_admin::AuthorizationAdministrationActor::local_cli();
    let event_request = authorization_admin::DurableGrantCreateRequest {
        principal_provider: "oidc:https://issuer.example",
        principal_subject: "history-target",
        participant_id: "rotary-main",
        capability: authorization::READ_MESSAGES,
        resource: None,
    };
    authorization_admin::create_durable_grant(&conn, &actor, &event_request).unwrap();
    drop(conn);

    let (verifier, token) = bearer_verifier_and_token("history-reader");
    fixture.router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );
    let body = |id| json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": "tools/call",
        "params": {
            "name": "blackboard_authorization_administration_history",
            "arguments": {
                "participant_id": "single-main",
                "filter_participant_id": "rotary-main"
            }
        }
    });
    let denied = request_with_authorization(&fixture.router, body(903), &format!("Bearer {token}")).await;
    let (_, denied_value) = response_json(denied).await;
    assert_eq!(tool_error_code(&denied_value), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('oidc:https://issuer.example', 'history-reader', 'single-main', ?1, ?2)",
        params![
            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE,
        ],
    )
    .unwrap();
    drop(conn);

    let allowed = request_with_authorization(&fixture.router, body(904), &format!("Bearer {token}")).await;
    let (_, allowed_value) = response_json(allowed).await;
    assert_eq!(allowed_value["result"]["isError"], false);
    assert_eq!(
        allowed_value["result"]["structuredContent"]["history"]["events"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}

#[tokio::test]
async fn mcp_authorization_administration_history_refuses_stale_schema_without_repair() {
    let fixture = fixture();
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main', ?1, ?2)",
        params![
            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE,
        ],
    )
    .unwrap();
    conn.execute("DROP TABLE authorization_admin_events", []).unwrap();
    drop(conn);

    let value = call_tool(
        &fixture.router,
        905,
        "blackboard_authorization_administration_history",
        administration_history_arguments(&fixture.single_secret, "single-main", None),
    )
    .await;
    assert_eq!(tool_error_code(&value), "authorization_schema_not_current");

    let conn = db::connect(&fixture.db_path).unwrap();
    let table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'authorization_admin_events'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(table_count, 0, "MCP history read repaired stale schema");
}

#[tokio::test]
async fn mcp_administration_history_schema_preserves_legacy_and_bearer_auth_projection() {
    let fixture = fixture();
    let response = request(
        &fixture.router,
        Method::POST,
        Some(json!({"jsonrpc":"2.0","id":906,"method":"tools/list","params":{}})),
    )
    .await;
    let (_, value) = response_json(response).await;
    let tool = value["result"]["tools"]
        .as_array().unwrap().iter()
        .find(|tool| tool["name"] == "blackboard_authorization_administration_history")
        .unwrap();
    assert_eq!(tool["outputSchema"], contract_schema::authorization_administration_history_envelope_schema());
    assert!(tool["inputSchema"]["required"].as_array().unwrap().iter().any(|field| field == "auth"));
    assert_eq!(tool["annotations"]["readOnlyHint"], true);
}
'''
if "mcp_authorization_administration_history_requires_explicit_hmac_authority_and_filters" in ct:
    raise RuntimeError("history MCP tests already exist")
ct_path.write_text(ct.rstrip() + append + "\n")

# Real stdio process inventory
stdio_path = Path("tests/mcp_stdio_server.rs")
stdio = stdio_path.read_text()
stdio = replace_once(stdio, '    assert_eq!(tools.len(), 10);', '    assert_eq!(tools.len(), 11);', "stdio tool count")
stdio = replace_once(
    stdio,
    '    assert!(tools\n        .iter()\n        .any(|tool| tool["name"] == "blackboard_authorization_policy"));',
    '    assert!(tools\n        .iter()\n        .any(|tool| tool["name"] == "blackboard_authorization_policy"));\n    assert!(tools.iter().any(|tool| {\n        tool["name"] == "blackboard_authorization_administration_history"\n    }));',
    "stdio history presence",
)
stdio_path.write_text(stdio)
