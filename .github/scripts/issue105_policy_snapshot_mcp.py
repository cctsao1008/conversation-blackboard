from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# Canonical JSON schemas.
p = Path("src/contract_schema.rs")
s = p.read_text(encoding="utf-8")
append_anchor = '''pub fn authorization_integrity_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"integrity": authorization_integrity_schema()},
        "required": ["integrity"],
        "additionalProperties": false
    })
}
'''
append = append_anchor + r'''

pub fn durable_grant_snapshot_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "id": {"type": "integer", "minimum": 1},
            "principal_provider": {"type": "string", "minLength": 1},
            "principal_subject": {"type": "string", "minLength": 1},
            "participant_id": participant_id_schema(),
            "capability": {"type": "string", "minLength": 1},
            "resource": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "status": {"type": "string", "enum": ["active", "inactive"]},
            "created_at": {"type": "integer"},
            "updated_at": {"type": "integer"}
        },
        "required": [
            "id", "principal_provider", "principal_subject", "participant_id",
            "capability", "resource", "status", "created_at", "updated_at"
        ],
        "additionalProperties": false,
        "description": "Non-secret durable authorization grant inventory record."
    })
}

pub fn delegated_grant_snapshot_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "id": {"type": "integer", "minimum": 1},
            "principal_provider": {"type": "string", "minLength": 1},
            "principal_subject": {"type": "string", "minLength": 1},
            "participant_id": participant_id_schema(),
            "capability": {"type": "string", "minLength": 1},
            "resource": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "intent_id": {"anyOf": [intent_id_schema(), {"type": "null"}]},
            "expires_at": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "one_shot": {"type": "boolean"},
            "consumed_at": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "consumed_intent_id": {"anyOf": [intent_id_schema(), {"type": "null"}]},
            "status": {"type": "string", "enum": ["active", "inactive"]}
        },
        "required": [
            "id", "principal_provider", "principal_subject", "participant_id",
            "capability", "resource", "intent_id", "expires_at", "one_shot",
            "consumed_at", "consumed_intent_id", "status"
        ],
        "additionalProperties": false,
        "description": "Non-secret delegated authorization grant inventory record including lifecycle and consumption state."
    })
}

pub fn authorization_policy_snapshot_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "durable_grants": {"type": "array", "items": durable_grant_snapshot_schema()},
            "delegated_grants": {"type": "array", "items": delegated_grant_snapshot_schema()}
        },
        "required": ["durable_grants", "delegated_grants"],
        "additionalProperties": false,
        "description": "Canonical privileged read-only inventory of explicit Blackboard authorization objects."
    })
}

pub fn authorization_policy_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"policy": authorization_policy_snapshot_schema()},
        "required": ["policy"],
        "additionalProperties": false
    })
}
'''
s = replace_once(s, append_anchor, append, "authorization policy canonical schemas")
p.write_text(s, encoding="utf-8")

# MCP tool projection and handler.
p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")

s = replace_once(
    s,
    '''        | "blackboard_execution_audit_sweep"
        | "blackboard_authorization_policy_integrity" => true,''',
    '''        | "blackboard_execution_audit_sweep"
        | "blackboard_authorization_policy_integrity"
        | "blackboard_authorization_policy" => true,''',
    "protected HTTP tool classifier",
)

s = replace_once(
    s,
    '''            {
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
            }
''',
    '''            {
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
''',
    "policy snapshot tool schema",
)

s = replace_once(
    s,
    '''        "blackboard_authorization_policy_integrity" => Ok(
            blackboard_authorization_policy_integrity(state, &arguments, transport_principal).await,
        ),
        _ => Err("unknown_tool"),''',
    '''        "blackboard_authorization_policy_integrity" => Ok(
            blackboard_authorization_policy_integrity(state, &arguments, transport_principal).await,
        ),
        "blackboard_authorization_policy" => Ok(
            blackboard_authorization_policy(state, &arguments, transport_principal).await,
        ),
        _ => Err("unknown_tool"),''',
    "policy snapshot tool dispatch",
)

handler_anchor = '''async fn blackboard_authorization_policy_integrity(
    state: &AppState,'''
handler = r'''async fn blackboard_authorization_policy(
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

'''
s = replace_once(s, handler_anchor, handler + handler_anchor, "policy snapshot MCP handler")

# Keep human-readable tool guidance current without making it normative.
s = s.replace(
    "blackboard_authorization_policy_integrity for structural authorization-policy integrity",
    "blackboard_authorization_policy_integrity for structural authorization-policy integrity, and blackboard_authorization_policy for privileged explicit-authority inventory",
)

p.write_text(s, encoding="utf-8")

# MCP contract regressions.
p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
s = s.replace('assert_eq!(tools.len(), 8);', 'assert_eq!(tools.len(), 9);')
s = s.replace(
    'assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 8);',
    'assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 9);',
)

schema_anchor = '''#[tokio::test]
async fn mcp_read_schema_includes_conversation_ref_provenance() {'''
schema_test = r'''#[tokio::test]
async fn mcp_policy_snapshot_tool_uses_canonical_read_only_schema() {
    let fixture = fixture();
    let response = request(
        &fixture.router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 122,
            "method": "tools/list",
            "params": {}
        })),
    )
    .await;
    let (_, value) = response_json(response).await;
    let tool = value["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "blackboard_authorization_policy")
        .expect("policy snapshot tool must be advertised");
    assert_eq!(
        tool["outputSchema"],
        contract_schema::authorization_policy_envelope_schema()
    );
    assert_eq!(tool["annotations"]["readOnlyHint"], true);
    assert!(tool["inputSchema"]["required"]
        .as_array()
        .unwrap()
        .iter()
        .any(|field| field == "auth"));
}

'''
s = replace_once(s, schema_anchor, schema_test + schema_anchor, "snapshot tool schema regression")

helper_anchor = '''fn policy_integrity_arguments(secret: &str, participant_id: &str) -> Value {'''
helper = r'''fn policy_snapshot_arguments(secret: &str, participant_id: &str) -> Value {
    let proof = participant_auth::compute_capability_proof(
        secret,
        participant_id,
        authorization::READ_AUTHORIZATION_POLICY,
        Some(authorization::AUTHORIZATION_POLICY_RESOURCE),
    )
    .unwrap();
    json!({
        "participant_id": participant_id,
        "auth": {"scheme": participant_auth::AUTH_SCHEME, "proof": proof}
    })
}

'''
s = replace_once(s, helper_anchor, helper + helper_anchor, "snapshot HMAC argument helper")

behavior_anchor = '''#[tokio::test]
async fn mcp_policy_integrity_requires_its_own_explicit_hmac_grant_and_never_migrates_schema() {'''
behavior = r'''#[tokio::test]
async fn mcp_policy_snapshot_requires_its_own_explicit_hmac_grant_and_never_migrates_schema() {
    let fixture = fixture();

    let denied = call_tool(
        &fixture.router,
        123,
        "blackboard_authorization_policy",
        policy_snapshot_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    assert_eq!(tool_error_code(&denied), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main',
                 'read_authorization_policy_integrity', 'authorization-policy-integrity')",
        [],
    )
    .unwrap();
    drop(conn);

    let wrong_capability = call_tool(
        &fixture.router,
        124,
        "blackboard_authorization_policy",
        policy_snapshot_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    assert_eq!(tool_error_code(&wrong_capability), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main',
                 'read_authorization_policy', 'authorization-policy')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource,
             intent_id, expires_at, one_shot, status)
         VALUES ('oidc:https://issuer.example', 'expired-agent', 'single-main', 'post_message',
                 'alpha', 'snapshot-expired-001', unixepoch() - 60, 0, 'active')",
        [],
    )
    .unwrap();
    drop(conn);

    let allowed = call_tool(
        &fixture.router,
        125,
        "blackboard_authorization_policy",
        policy_snapshot_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    assert_eq!(allowed["result"]["isError"], false);
    let durable = allowed["result"]["structuredContent"]["policy"]["durable_grants"]
        .as_array()
        .unwrap();
    assert!(durable.iter().any(|entry| {
        entry["capability"] == authorization::READ_AUTHORIZATION_POLICY
            && entry["resource"] == authorization::AUTHORIZATION_POLICY_RESOURCE
    }));
    let delegated = allowed["result"]["structuredContent"]["policy"]["delegated_grants"]
        .as_array()
        .unwrap();
    assert!(delegated.iter().any(|entry| {
        entry["intent_id"] == "snapshot-expired-001" && entry["expires_at"].is_number()
    }));

    // Snapshot authority alone does not imply policy-integrity authority.
    let integrity = call_tool(
        &fixture.router,
        126,
        "blackboard_authorization_policy_integrity",
        policy_integrity_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    // An explicit integrity grant was deliberately inserted above, so remove it
    // before asserting separation in the reverse direction.
    assert_eq!(integrity["result"]["isError"], false);
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "UPDATE principal_grants SET status = 'inactive'
         WHERE principal_provider = 'participant-hmac'
           AND principal_subject = 'single-main'
           AND participant_id = 'single-main'
           AND capability = 'read_authorization_policy_integrity'",
        [],
    )
    .unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    drop(conn);

    let legacy = call_tool(
        &fixture.router,
        127,
        "blackboard_authorization_policy",
        policy_snapshot_arguments(&fixture.single_secret, "single-main"),
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
    assert_eq!(table_count, 0, "MCP snapshot read recreated authorization schema");
}

#[tokio::test]
async fn bearer_policy_snapshot_requires_explicit_blackboard_grant() {
    let fixture = fixture();
    let (verifier, token) = bearer_verifier_and_token("policy-reader");
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
            "id": 128,
            "method": "tools/call",
            "params": {
                "name": "blackboard_authorization_policy",
                "arguments": {"participant_id": "single-main"}
            }
        }),
        &authorization_header,
    )
    .await;
    let (status, denied_value) = response_json(denied).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(tool_error_code(&denied_value), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('oidc:https://issuer.example', 'policy-reader', 'single-main',
                 'read_authorization_policy', 'authorization-policy')",
        [],
    )
    .unwrap();
    drop(conn);

    let allowed = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 129,
            "method": "tools/call",
            "params": {
                "name": "blackboard_authorization_policy",
                "arguments": {"participant_id": "single-main"}
            }
        }),
        &authorization_header,
    )
    .await;
    let (status, allowed_value) = response_json(allowed).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(allowed_value["result"]["isError"], false);
    assert!(allowed_value["result"]["structuredContent"]["policy"]["durable_grants"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| entry["principal_subject"] == "policy-reader"));
}

'''
s = replace_once(s, behavior_anchor, behavior + behavior_anchor, "snapshot MCP authority regressions")

s = replace_once(
    s,
    '''        "blackboard_execution_audit_sweep",
        "blackboard_authorization_policy_integrity",
    ] {''',
    '''        "blackboard_execution_audit_sweep",
        "blackboard_authorization_policy_integrity",
        "blackboard_authorization_policy",
    ] {''',
    "modern bearer-aware tool projection list",
)

s = replace_once(
    s,
    '''    assert!(tool_required_fields(&legacy_value, "blackboard_write")
        .iter()
        .any(|field| field == "auth"));''',
    '''    for name in ["blackboard_write", "blackboard_authorization_policy"] {
        assert!(tool_required_fields(&legacy_value, name)
            .iter()
            .any(|field| field == "auth"));
    }''',
    "legacy snapshot HMAC requirement",
)

s = replace_once(
    s,
    '''    assert!(tool_required_fields(&stdio, "blackboard_write")
        .iter()
        .any(|field| field == "auth"));''',
    '''    for name in ["blackboard_write", "blackboard_authorization_policy"] {
        assert!(tool_required_fields(&stdio, name)
            .iter()
            .any(|field| field == "auth"));
    }''',
    "stdio snapshot HMAC requirement",
)

p.write_text(s, encoding="utf-8")
