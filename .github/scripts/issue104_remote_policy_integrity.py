from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# authorization kernel
p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''pub const READ_EXECUTION_AUDIT_SWEEP: &str = "read_execution_audit_sweep";\npub const EXECUTION_AUDIT_SWEEP_RESOURCE: &str = "execution-audit-sweep";\npub const MANAGE_CHANNELS: &str = "manage_channels";\n\nconst KNOWN_CAPABILITIES: [&str; 7] = [\n    READ_MESSAGES,\n    POST_MESSAGE,\n    REPLY,\n    READ_EXECUTION_RECEIPT,\n    READ_EXECUTION_AUDIT,\n    READ_EXECUTION_AUDIT_SWEEP,\n    MANAGE_CHANNELS,\n];''',
    '''pub const READ_EXECUTION_AUDIT_SWEEP: &str = "read_execution_audit_sweep";\npub const EXECUTION_AUDIT_SWEEP_RESOURCE: &str = "execution-audit-sweep";\npub const READ_AUTHORIZATION_POLICY_INTEGRITY: &str = "read_authorization_policy_integrity";\npub const AUTHORIZATION_POLICY_INTEGRITY_RESOURCE: &str = "authorization-policy-integrity";\npub const MANAGE_CHANNELS: &str = "manage_channels";\n\nconst KNOWN_CAPABILITIES: [&str; 8] = [\n    READ_MESSAGES,\n    POST_MESSAGE,\n    REPLY,\n    READ_EXECUTION_RECEIPT,\n    READ_EXECUTION_AUDIT,\n    READ_EXECUTION_AUDIT_SWEEP,\n    READ_AUTHORIZATION_POLICY_INTEGRITY,\n    MANAGE_CHANNELS,\n];''',
    "authorization capability constants",
)
s = replace_once(
    s,
    '''        let implicit_resource =\n            (capability == READ_EXECUTION_AUDIT_SWEEP).then_some(EXECUTION_AUDIT_SWEEP_RESOURCE);''',
    '''        let implicit_resource = match capability {\n            READ_EXECUTION_AUDIT_SWEEP => Some(EXECUTION_AUDIT_SWEEP_RESOURCE),\n            READ_AUTHORIZATION_POLICY_INTEGRITY => Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),\n            _ => None,\n        };''',
    "effective grants global resources",
)
s = replace_once(
    s,
    '''    if self_authenticated {\n        if capability == READ_EXECUTION_AUDIT_SWEEP {''',
    '''    if self_authenticated {\n        if capability == READ_AUTHORIZATION_POLICY_INTEGRITY {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE))\n            .then_some("implicit_human_web_admin_authorization_policy_integrity");\n        }\n        if capability == READ_EXECUTION_AUDIT_SWEEP {''',
    "implicit policy integrity authority",
)
anchor = '''    #[test]\n    fn explicit_grant_can_authorize_participant_hmac_audit_sweep() {'''
tests = r'''    #[test]
    fn policy_integrity_implicit_authority_is_human_web_admin_only() {
        let (_dir, conn) = setup();
        let human = Principal {
            provider: "human-web".into(),
            subject: "maker-main".into(),
        };
        let hmac = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        let github = Principal {
            provider: "github".into(),
            subject: "543608".into(),
        };

        for principal in [&human, &hmac, &github] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                READ_AUTHORIZATION_POLICY_INTEGRITY,
                Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            )
            .unwrap());
        }

        conn.execute(
            "UPDATE web_participants SET role = 'admin' WHERE participant_id = 'maker-main'",
            [],
        )
        .unwrap();
        let decision = evaluate_authorization(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            None,
        )
        .unwrap();
        assert!(decision.allowed);
        assert_eq!(
            decision.reason,
            "implicit_human_web_admin_authorization_policy_integrity"
        );
        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some("wrong-resource"),
        )
        .unwrap());

        let grants = effective_grants(&conn, &human, "maker-main").unwrap();
        assert!(grants.iter().any(|grant| {
            grant.capability == READ_AUTHORIZATION_POLICY_INTEGRITY
                && grant.resource.as_deref() == Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE)
                && grant.origin == "implicit"
        }));
    }

    #[test]
    fn explicit_grant_can_authorize_participant_hmac_policy_integrity() {
        let (_dir, conn) = setup();
        ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('participant-hmac', 'maker-main', 'maker-main',
                     'read_authorization_policy_integrity', 'authorization-policy-integrity')",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        let decision = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            None,
        )
        .unwrap();
        assert!(decision.allowed);
        assert_eq!(decision.reason, "explicit_durable_grant_match");
    }

''' + anchor
s = replace_once(s, anchor, tests, "policy integrity authority tests")
p.write_text(s, encoding="utf-8")

# canonical contract schemas
p = Path("src/contract_schema.rs")
s = p.read_text(encoding="utf-8")
append = r'''

pub fn authorization_integrity_violation_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "store": {"type": "string"},
            "grant_id": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "participant_id": {"anyOf": [participant_id_schema(), {"type": "null"}]},
            "detail": {"type": "string"}
        },
        "required": ["kind", "store", "grant_id", "participant_id", "detail"],
        "additionalProperties": false,
        "description": "Non-secret read-only classification of malformed or ambiguous authorization-policy state."
    })
}

pub fn authorization_integrity_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "valid": {"type": "boolean"},
            "durable_grants_scanned": {"type": "integer", "minimum": 0},
            "delegated_grants_scanned": {"type": "integer", "minimum": 0},
            "violations": {
                "type": "array",
                "items": authorization_integrity_violation_schema()
            }
        },
        "required": ["valid", "durable_grants_scanned", "delegated_grants_scanned", "violations"],
        "additionalProperties": false,
        "description": "Canonical read-only integrity report over Blackboard authorization policy objects."
    })
}

pub fn authorization_integrity_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"integrity": authorization_integrity_schema()},
        "required": ["integrity"],
        "additionalProperties": false
    })
}
'''
if "pub fn authorization_integrity_envelope_schema()" in s:
    raise SystemExit("authorization integrity schemas already present")
s = s.rstrip() + append + "\n"
p.write_text(s, encoding="utf-8")

# REST projection
p = Path("src/access_api.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''        .route("/api/execution-audit/sweep", get(execution_audit_sweep))\n        .with_state(state)''',
    '''        .route("/api/execution-audit/sweep", get(execution_audit_sweep))\n        .route(\n            "/api/authorization-policy/integrity",\n            get(authorization_policy_integrity),\n        )\n        .with_state(state)''',
    "REST policy integrity route",
)
anchor = '''fn principal_for_headers(headers: &HeaderMap, resolved: &Identity) -> execution::Principal {'''
handler = r'''async fn authorization_policy_integrity(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (allowed, schema_current, report) = with_db(&state, move |conn| {
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        )?;
        if !allowed {
            return Ok((false, true, None));
        }
        let schema_current = authorization::authorization_integrity_schema_current(conn)?;
        if !schema_current {
            return Ok((true, false, None));
        }
        Ok((
            true,
            true,
            Some(authorization::audit_authorization_integrity(conn)?),
        ))
    })
    .await?;
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    if !schema_current {
        return Err(AccessApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "authorization_schema_not_current",
        ));
    }
    let report = report.expect("authorized current-schema policy integrity read must produce report");
    Ok(json_response(StatusCode::OK, json!({"integrity": report})))
}

''' + anchor
s = replace_once(s, anchor, handler, "REST policy integrity handler")
p.write_text(s, encoding="utf-8")

# MCP projection
p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''        | "blackboard_execution_audit_integrity"\n        | "blackboard_execution_audit_sweep" => true,''',
    '''        | "blackboard_execution_audit_integrity"\n        | "blackboard_execution_audit_sweep"\n        | "blackboard_authorization_policy_integrity" => true,''',
    "MCP protected tool classifier",
)
s = replace_once(
    s,
    '''                "outputSchema": contract_schema::execution_audit_sweep_envelope_schema(),\n                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}\n            }\n        ]''',
    '''                "outputSchema": contract_schema::execution_audit_sweep_envelope_schema(),\n                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}\n            },\n            {\n                "name": "blackboard_authorization_policy_integrity",\n                "title": "Verify Blackboard Authorization Policy Integrity",\n                "description": "Run the privileged canonical read-only integrity audit over durable and delegated authorization policy objects without repairing policy state.",\n                "inputSchema": {\n                    "type": "object",\n                    "properties": {\n                        "participant_id": contract_schema::participant_id_schema(),\n                        "auth": auth_schema()\n                    },\n                    "required": ["participant_id", "auth"],\n                    "additionalProperties": false\n                },\n                "outputSchema": contract_schema::authorization_integrity_envelope_schema(),\n                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}\n            }\n        ]''',
    "MCP policy integrity tool schema",
)
s = replace_once(
    s,
    '''        "blackboard_execution_audit_sweep" => {\n            Ok(blackboard_execution_audit_sweep(state, &arguments, transport_principal).await)\n        }\n        _ => Err("unknown_tool"),''',
    '''        "blackboard_execution_audit_sweep" => {\n            Ok(blackboard_execution_audit_sweep(state, &arguments, transport_principal).await)\n        }\n        "blackboard_authorization_policy_integrity" => {\n            Ok(blackboard_authorization_policy_integrity(\n                state,\n                &arguments,\n                transport_principal,\n            )\n            .await)\n        }\n        _ => Err("unknown_tool"),''',
    "MCP policy integrity dispatcher",
)
anchor = '''async fn blackboard_read(\n    state: &AppState,'''
handler = r'''async fn blackboard_authorization_policy_integrity(
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
        if !authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        )? {
            return Ok((true, None));
        }
        let schema_current = authorization::authorization_integrity_schema_current(conn)?;
        if !schema_current {
            return Ok((false, None));
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

''' + anchor
s = replace_once(s, anchor, handler, "MCP policy integrity handler")
s = replace_once(
    s,
    '''Use blackboard_execution_audit_sweep only with privileged corpus-wide audit authority. Authenticated calls use hmac-sha256-v1 proofs bound to the canonical request.''',
    '''Use blackboard_execution_audit_sweep only with privileged corpus-wide audit authority, and blackboard_authorization_policy_integrity only with privileged global policy-integrity authority. Authenticated calls use hmac-sha256-v1 proofs bound to the canonical request.''',
    "MCP initialize instructions",
)
p.write_text(s, encoding="utf-8")
