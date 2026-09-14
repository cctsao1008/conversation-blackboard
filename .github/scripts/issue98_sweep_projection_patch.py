from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing marker: {label}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Authorization: dedicated privileged sweep capability and resource.
# ---------------------------------------------------------------------------
p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")
old = '''pub const READ_MESSAGES: &str = "read_messages";
pub const POST_MESSAGE: &str = "post_message";
pub const REPLY: &str = "reply";
pub const READ_EXECUTION_RECEIPT: &str = "read_execution_receipt";
pub const READ_EXECUTION_AUDIT: &str = "read_execution_audit";
pub const MANAGE_CHANNELS: &str = "manage_channels";

const KNOWN_CAPABILITIES: [&str; 6] = [
    READ_MESSAGES,
    POST_MESSAGE,
    REPLY,
    READ_EXECUTION_RECEIPT,
    READ_EXECUTION_AUDIT,
    MANAGE_CHANNELS,
];
'''
new = '''pub const READ_MESSAGES: &str = "read_messages";
pub const POST_MESSAGE: &str = "post_message";
pub const REPLY: &str = "reply";
pub const READ_EXECUTION_RECEIPT: &str = "read_execution_receipt";
pub const READ_EXECUTION_AUDIT: &str = "read_execution_audit";
pub const READ_EXECUTION_AUDIT_SWEEP: &str = "read_execution_audit_sweep";
pub const EXECUTION_AUDIT_SWEEP_RESOURCE: &str = "execution-audit-sweep";
pub const MANAGE_CHANNELS: &str = "manage_channels";

const KNOWN_CAPABILITIES: [&str; 7] = [
    READ_MESSAGES,
    POST_MESSAGE,
    REPLY,
    READ_EXECUTION_RECEIPT,
    READ_EXECUTION_AUDIT,
    READ_EXECUTION_AUDIT_SWEEP,
    MANAGE_CHANNELS,
];
'''
s = replace_once(s, old, new, "authorization capability constants")

s = replace_once(
    s,
    '''implicit_authority_reason(principal, participant_id, capability, &participant)''',
    '''implicit_authority_reason(
        principal,
        participant_id,
        capability,
        resource,
        &participant,
    )''',
    "implicit authority invocation",
)

old = '''    let mut grants = explicit.clone();
    for capability in KNOWN_CAPABILITIES {
        if explicit.iter().any(|grant| grant.capability == capability) {
            continue;
        }
        if authorize(conn, principal, participant_id, capability, None)? {
            grants.push(EffectiveGrant {
                capability: capability.to_owned(),
                resource: None,
                origin: "implicit".to_owned(),
            });
        }
    }
'''
new = '''    let mut grants = explicit.clone();
    for capability in KNOWN_CAPABILITIES {
        if explicit.iter().any(|grant| grant.capability == capability) {
            continue;
        }
        let implicit_resource = (capability == READ_EXECUTION_AUDIT_SWEEP)
            .then_some(EXECUTION_AUDIT_SWEEP_RESOURCE);
        if authorize(
            conn,
            principal,
            participant_id,
            capability,
            implicit_resource,
        )? {
            grants.push(EffectiveGrant {
                capability: capability.to_owned(),
                resource: implicit_resource.map(str::to_owned),
                origin: "implicit".to_owned(),
            });
        }
    }
'''
s = replace_once(s, old, new, "effective grants implicit sweep resource")

old = '''fn implicit_authority_reason(
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    participant: &ParticipantPolicyRow,
) -> Option<&'static str> {
'''
new = '''fn implicit_authority_reason(
    principal: &Principal,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    participant: &ParticipantPolicyRow,
) -> Option<&'static str> {
'''
s = replace_once(s, old, new, "implicit authority signature")

old = '''    if self_authenticated {
        if capability == MANAGE_CHANNELS {
'''
new = '''    if self_authenticated {
        if capability == READ_EXECUTION_AUDIT_SWEEP {
            return (principal.provider == "human-web"
                && participant.role == "admin"
                && resource == Some(EXECUTION_AUDIT_SWEEP_RESOURCE))
            .then_some("implicit_human_web_admin_audit_sweep");
        }
        if capability == MANAGE_CHANNELS {
'''
s = replace_once(s, old, new, "privileged sweep implicit authority")

end_marker = '''    #[test]
    fn explicit_execution_audit_scope_suppresses_implicit_fallback() {
        let (_dir, conn) = setup();
        ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('participant-hmac', 'maker-main', 'maker-main', 'read_execution_audit', 'intent-allowed')",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "participant-hmac".into(),
            subject: "maker-main".into(),
        };
        assert!(authorize(
            &conn,
            &principal,
            "maker-main",
            READ_EXECUTION_AUDIT,
            Some("intent-allowed"),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &principal,
            "maker-main",
            READ_EXECUTION_AUDIT,
            Some("intent-denied"),
        )
        .unwrap());
    }
}
'''
replacement = end_marker[:-2] + r'''

    #[test]
    fn audit_sweep_implicit_authority_is_human_web_admin_only() {
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

        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &hmac,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &github,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
        )
        .unwrap());

        conn.execute(
            "UPDATE web_participants SET role = 'admin' WHERE participant_id = 'maker-main'",
            [],
        )
        .unwrap();
        let decision = evaluate_authorization(
            &conn,
            &human,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            None,
        )
        .unwrap();
        assert!(decision.allowed);
        assert_eq!(decision.reason, "implicit_human_web_admin_audit_sweep");
        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_EXECUTION_AUDIT_SWEEP,
            Some("wrong-resource"),
        )
        .unwrap());

        let grants = effective_grants(&conn, &human, "maker-main").unwrap();
        assert!(grants.iter().any(|grant| {
            grant.capability == READ_EXECUTION_AUDIT_SWEEP
                && grant.resource.as_deref() == Some(EXECUTION_AUDIT_SWEEP_RESOURCE)
                && grant.origin == "implicit"
        }));
    }

    #[test]
    fn explicit_grant_can_authorize_participant_hmac_audit_sweep() {
        let (_dir, conn) = setup();
        ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('participant-hmac', 'maker-main', 'maker-main',
                     'read_execution_audit_sweep', 'execution-audit-sweep')",
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
            READ_EXECUTION_AUDIT_SWEEP,
            Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            None,
        )
        .unwrap();
        assert!(decision.allowed);
        assert_eq!(decision.reason, "explicit_durable_grant_match");
    }
}
'''
s = replace_once(s, end_marker, replacement, "authorization sweep tests")
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# Canonical contract schemas for orphan evidence and sweep report.
# ---------------------------------------------------------------------------
p = Path("src/contract_schema.rs")
s = p.read_text(encoding="utf-8")
if "pub fn execution_audit_sweep_schema()" in s:
    raise SystemExit("sweep contract schema already present")
s += r'''

pub fn execution_audit_orphan_evidence_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "participant_id": {"anyOf": [participant_id_schema(), {"type": "null"}]},
            "intent_id": intent_id_schema(),
            "reference": {"anyOf": [{"type": "string"}, {"type": "null"}]}
        },
        "required": ["kind", "participant_id", "intent_id", "reference"],
        "additionalProperties": false,
        "description": "Read-only classification of durable execution evidence that cannot be attached to a committed receipt identity."
    })
}

pub fn execution_audit_sweep_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "valid": {"type": "boolean"},
            "executions_scanned": {"type": "integer", "minimum": 0},
            "invalid_executions": {
                "type": "array",
                "items": execution_audit_integrity_schema()
            },
            "orphan_evidence": {
                "type": "array",
                "items": execution_audit_orphan_evidence_schema()
            }
        },
        "required": ["valid", "executions_scanned", "invalid_executions", "orphan_evidence"],
        "additionalProperties": false,
        "description": "Canonical database-wide read-only execution audit integrity sweep."
    })
}

pub fn execution_audit_sweep_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"sweep": execution_audit_sweep_schema()},
        "required": ["sweep"],
        "additionalProperties": false
    })
}
'''
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# REST projection: privileged global sweep endpoint.
# ---------------------------------------------------------------------------
p = Path("src/access_api.rs")
s = p.read_text(encoding="utf-8")
route_marker = '''        .route(
            "/api/executions/{intent_id}/audit/integrity",
            get(execution_audit_integrity),
        )
        .with_state(state)
'''
route_replacement = '''        .route(
            "/api/executions/{intent_id}/audit/integrity",
            get(execution_audit_integrity),
        )
        .route("/api/execution-audit/sweep", get(execution_audit_sweep))
        .with_state(state)
'''
s = replace_once(s, route_marker, route_replacement, "REST sweep route")

handler_marker = '''fn principal_for_headers(headers: &HeaderMap, resolved: &Identity) -> execution::Principal {
'''
handler = r'''async fn execution_audit_sweep(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (allowed, report) = with_db(&state, move |conn| {
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT_SWEEP,
            Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
        )?;
        let report = if allowed {
            Some(execution::sweep_execution_audit_integrity(conn)?)
        } else {
            None
        };
        Ok((allowed, report))
    })
    .await?;
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    let report = report.expect("authorized audit sweep must produce a report");
    Ok(json_response(StatusCode::OK, json!({"sweep": report})))
}

'''
s = replace_once(s, handler_marker, handler + handler_marker, "REST sweep handler")
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# MCP projection: same capability/resource and canonical sweep semantics.
# ---------------------------------------------------------------------------
p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''and blackboard_execution_audit_integrity for structural audit verification.''',
    '''and blackboard_execution_audit_integrity for structural audit verification. Use blackboard_execution_audit_sweep only with privileged corpus-wide audit authority.''',
    "MCP instructions",
)

tool_marker = '''            {
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
            }
'''
tool_replacement = tool_marker[:-1] + r''',
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
            }
'''
s = replace_once(s, tool_marker, tool_replacement, "MCP sweep tool schema")

old = '''        "blackboard_execution_audit_integrity" => {
            Ok(blackboard_execution_audit_integrity(state, &arguments).await)
        }
        _ => Err("unknown_tool"),
'''
new = '''        "blackboard_execution_audit_integrity" => {
            Ok(blackboard_execution_audit_integrity(state, &arguments).await)
        }
        "blackboard_execution_audit_sweep" => {
            Ok(blackboard_execution_audit_sweep(state, &arguments).await)
        }
        _ => Err("unknown_tool"),
'''
s = replace_once(s, old, new, "MCP sweep dispatch")

fn_marker = '''async fn blackboard_read(state: &AppState, arguments: &Map<String, Value>) -> Value {
'''
sweep_fn = r'''async fn blackboard_execution_audit_sweep(
    state: &AppState,
    arguments: &Map<String, Value>,
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
    if let Err(code) = resolve_capability_identity(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_AUDIT_SWEEP,
        Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
    )
    .await
    {
        return tool_error(code);
    }
    let principal = execution::Principal {
        provider: "participant-hmac".to_owned(),
        subject: participant_id.clone(),
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

'''
s = replace_once(s, fn_marker, sweep_fn + fn_marker, "MCP sweep implementation")
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# OpenAPI projection.
# ---------------------------------------------------------------------------
p = Path("integrations/openapi.yaml")
s = p.read_text(encoding="utf-8")
path_marker = '''  /api/admin/channels:
'''
path_block = '''  /api/execution-audit/sweep:
    get:
      operationId: blackboardExecutionAuditSweep
      summary: Verify database-wide execution audit integrity.
      description: Privileged projection of the canonical read-only execution audit sweep. Invalid integrity state is returned as report data with HTTP 200; the endpoint does not repair history.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      responses:
        '200':
          description: Canonical database-wide execution audit sweep report.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ExecutionAuditSweepEnvelope'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'

'''
s = replace_once(s, path_marker, path_block + path_marker, "OpenAPI sweep path")

schema_marker = '''    Identity:
'''
schema_block = '''    ExecutionAuditOrphanEvidence:
      type: object
      additionalProperties: false
      required: [kind, participant_id, intent_id, reference]
      description: Read-only classification of durable execution evidence that cannot be attached to a committed receipt identity.
      properties:
        kind:
          type: string
        participant_id:
          anyOf:
            - $ref: '#/components/schemas/ParticipantId'
            - type: 'null'
        intent_id:
          $ref: '#/components/schemas/IntentId'
        reference:
          type: [string, 'null']

    ExecutionAuditSweepReport:
      type: object
      additionalProperties: false
      required: [valid, executions_scanned, invalid_executions, orphan_evidence]
      description: Canonical database-wide read-only execution audit integrity sweep.
      properties:
        valid:
          type: boolean
        executions_scanned:
          type: integer
          minimum: 0
        invalid_executions:
          type: array
          items:
            $ref: '#/components/schemas/ExecutionAuditIntegrityReport'
        orphan_evidence:
          type: array
          items:
            $ref: '#/components/schemas/ExecutionAuditOrphanEvidence'

    ExecutionAuditSweepEnvelope:
      type: object
      additionalProperties: false
      required: [sweep]
      properties:
        sweep:
          $ref: '#/components/schemas/ExecutionAuditSweepReport'

'''
s = replace_once(s, schema_marker, schema_block + schema_marker, "OpenAPI sweep schemas")
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# Contract parity coverage.
# ---------------------------------------------------------------------------
p = Path("src/contract_parity_tests.rs")
s = p.read_text(encoding="utf-8")
if "execution_audit_sweep_contracts_match_canonical_rust_shape" in s:
    raise SystemExit("sweep contract parity test already present")
s += r'''

#[test]
fn execution_audit_sweep_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert_eq!(
        names(
            &api,
            "/components/schemas/ExecutionAuditOrphanEvidence/properties"
        ),
        names(
            &contract_schema::execution_audit_orphan_evidence_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/ExecutionAuditSweepReport/properties"
        ),
        names(&contract_schema::execution_audit_sweep_schema(), "/properties")
    );
    assert!(api["paths"].get("/api/execution-audit/sweep").is_some());
}
'''
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# MCP regression coverage.
# ---------------------------------------------------------------------------
p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''use crate::{authorization, db, http::AppState, identity, mcp, participant_auth};''',
    '''use crate::{authorization, contract_schema, db, http::AppState, identity, mcp, participant_auth};''',
    "MCP test imports",
)
s = replace_once(
    s,
    '''assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 6);''',
    '''assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 7);''',
    "MCP stdio tool count",
)
s = replace_once(
    s,
    '''assert_eq!(tools.len(), 6);''',
    '''assert_eq!(tools.len(), 7);''',
    "MCP HTTP tool count",
)

helper_marker = '''#[tokio::test]
async fn mcp_access_context_and_execution_receipt_project_shared_domain_state() {
'''
helper = r'''fn sweep_arguments(secret: &str, participant_id: &str) -> Value {
    let proof = participant_auth::compute_capability_proof(
        secret,
        participant_id,
        authorization::READ_EXECUTION_AUDIT_SWEEP,
        Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
    )
    .unwrap();
    json!({
        "participant_id": participant_id,
        "auth": {"scheme": participant_auth::AUTH_SCHEME, "proof": proof}
    })
}

#[tokio::test]
async fn mcp_audit_sweep_is_privileged_and_projects_canonical_report() {
    let fixture = fixture();

    let listed = mcp::stdio_dispatch(
        &AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        &json!({"jsonrpc": "2.0", "id": 60, "method": "tools/list", "params": {}}),
    )
    .await
    .unwrap();
    let tool = listed["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "blackboard_execution_audit_sweep")
        .expect("sweep tool must be advertised");
    assert_eq!(
        tool["outputSchema"],
        contract_schema::execution_audit_sweep_envelope_schema()
    );
    assert_eq!(tool["annotations"]["readOnlyHint"], true);

    let denied = call_tool(
        &fixture.router,
        61,
        "blackboard_execution_audit_sweep",
        sweep_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    assert_eq!(tool_error_code(&denied), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main',
                 'read_execution_audit_sweep', 'execution-audit-sweep')",
        [],
    )
    .unwrap();
    drop(conn);

    let clean = call_tool(
        &fixture.router,
        62,
        "blackboard_execution_audit_sweep",
        sweep_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    assert!(!clean["result"]["isError"].as_bool().unwrap());
    assert_eq!(clean["result"]["structuredContent"]["sweep"]["valid"], true);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)
         VALUES ('ghost-main', 'orphan-sweep', 'hash', 99)",
        [],
    )
    .unwrap();
    drop(conn);

    let invalid = call_tool(
        &fixture.router,
        63,
        "blackboard_execution_audit_sweep",
        sweep_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    assert!(!invalid["result"]["isError"].as_bool().unwrap());
    assert_eq!(invalid["result"]["structuredContent"]["sweep"]["valid"], false);
    assert!(invalid["result"]["structuredContent"]["sweep"]["orphan_evidence"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| entry["kind"] == "orphan_navigation_reservation"));
}

'''
s = replace_once(s, helper_marker, helper + helper_marker, "MCP sweep tests")
p.write_text(s, encoding="utf-8")


# ---------------------------------------------------------------------------
# REST regression coverage.
# ---------------------------------------------------------------------------
p = Path("src/http_contract_tests.rs")
s = p.read_text(encoding="utf-8")
if "execution_audit_sweep_http_requires_privileged_authority" in s:
    raise SystemExit("REST sweep test already present")
s += r'''

#[tokio::test]
async fn execution_audit_sweep_http_requires_privileged_authority_and_returns_invalid_data() {
    let fixture = fixture("sweep");
    let audit_router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let session = web_auth::issue_web_session(&fixture.participant_id);

    let ordinary = request(
        &audit_router,
        Method::GET,
        "/api/execution-audit/sweep",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "UPDATE web_participants SET role = 'admin' WHERE participant_id = ?1",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);

    let clean = request(
        &audit_router,
        Method::GET,
        "/api/execution-audit/sweep",
        Some(&session.token),
        None,
    )
    .await;
    let (clean_status, clean_body) = response_json(clean).await;
    assert_eq!(clean_status, StatusCode::OK);
    assert_eq!(clean_body["sweep"]["valid"], true);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)
         VALUES ('ghost-main', 'orphan-http-sweep', 'hash', 99)",
        [],
    )
    .unwrap();
    drop(conn);

    let invalid = request(
        &audit_router,
        Method::GET,
        "/api/execution-audit/sweep",
        Some(&session.token),
        None,
    )
    .await;
    let (invalid_status, invalid_body) = response_json(invalid).await;
    assert_eq!(invalid_status, StatusCode::OK);
    assert_eq!(invalid_body["sweep"]["valid"], false);
    assert!(invalid_body["sweep"]["orphan_evidence"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| entry["kind"] == "orphan_navigation_reservation"));
}
'''
p.write_text(s, encoding="utf-8")
