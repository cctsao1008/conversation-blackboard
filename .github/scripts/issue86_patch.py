from pathlib import Path
import json

# --- access_api.rs: REST route + handler ---
p = Path('src/access_api.rs')
s = p.read_text(encoding='utf-8')
route = '        .route("/api/executions/{intent_id}/audit", get(execution_audit))\n'
if '"/api/executions/{intent_id}/audit/integrity"' not in s:
    if route not in s:
        raise SystemExit('access_api audit route marker not found')
    s = s.replace(route, route + '        .route(\n            "/api/executions/{intent_id}/audit/integrity",\n            get(execution_audit_integrity),\n        )\n', 1)

marker = '\nfn principal_for_headers(headers: &HeaderMap, resolved: &Identity) -> execution::Principal {'
if 'async fn execution_audit_integrity(' not in s:
    if marker not in s:
        raise SystemExit('access_api principal marker not found')
    handler = r'''

async fn execution_audit_integrity(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(intent_id): Path<String>,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let intent_id = execution::normalize_intent_id(&intent_id)
        .map_err(|_| AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_intent_id"))?;
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let (allowed, report) = with_db(&state, move |conn| {
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )?;
        let report = if allowed {
            Some(execution::verify_execution_audit_integrity(
                conn,
                &lookup_participant,
                &lookup_intent,
            )?)
        } else {
            None
        };
        Ok((allowed, report))
    })
    .await?;
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    let report = report.expect("authorized integrity verification must produce a report");
    Ok(json_response(StatusCode::OK, json!({"integrity": report})))
}
'''
    s = s.replace(marker, handler + marker, 1)
p.write_text(s, encoding='utf-8')

# --- contract_schema.rs: canonical integrity schema ---
p = Path('src/contract_schema.rs')
s = p.read_text(encoding='utf-8')
if 'pub fn execution_audit_integrity_schema()' not in s:
    s += r'''

pub fn execution_audit_integrity_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "participant_id": participant_id_schema(),
            "intent_id": intent_id_schema(),
            "valid": {"type": "boolean"},
            "checks": {"type": "array", "items": {"type": "string"}},
            "violations": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["participant_id", "intent_id", "valid", "checks", "violations"],
        "additionalProperties": false,
        "description": "Read-only structural integrity report over committed execution evidence. It does not re-evaluate historical authorization policy."
    })
}

pub fn execution_audit_integrity_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"integrity": execution_audit_integrity_schema()},
        "required": ["integrity"],
        "additionalProperties": false
    })
}
'''
p.write_text(s, encoding='utf-8')

# --- adapter_profile.rs ---
p = Path('src/adapter_profile.rs')
s = p.read_text(encoding='utf-8')
needle = '        "execution_audit",\n'
if '        "execution_audit_integrity",\n' not in s:
    if s.count(needle) < 2:
        raise SystemExit('adapter profile audit capability markers not found')
    s = s.replace(needle, needle + '        "execution_audit_integrity",\n', 2)
p.write_text(s, encoding='utf-8')

# --- mcp.rs: advertise, dispatch, execute canonical verifier ---
p = Path('src/mcp.rs')
s = p.read_text(encoding='utf-8')
s = s.replace(
    'blackboard_execution_receipt for semantic execution read-back, and blackboard_execution_audit for immutable historical audit.',
    'blackboard_execution_receipt for semantic execution read-back, blackboard_execution_audit for immutable historical audit, and blackboard_execution_audit_integrity for structural audit verification.',
)
if '"name": "blackboard_execution_audit_integrity"' not in s:
    audit_tool = r'''            {
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
            }
'''
    replacement = audit_tool[:-2] + r'''            },
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
            }
'''
    if audit_tool not in s:
        raise SystemExit('MCP audit tool block not found')
    s = s.replace(audit_tool, replacement, 1)

dispatch = '        "blackboard_execution_audit" => Ok(blackboard_execution_audit(state, &arguments).await),\n'
if '"blackboard_execution_audit_integrity" => ' not in s:
    if dispatch not in s:
        raise SystemExit('MCP audit dispatch marker not found')
    s = s.replace(dispatch, dispatch + '        "blackboard_execution_audit_integrity" => {\n            Ok(blackboard_execution_audit_integrity(state, &arguments).await)\n        }\n', 1)

function_marker = '\nasync fn blackboard_read(state: &AppState, arguments: &Map<String, Value>) -> Value {'
if 'async fn blackboard_execution_audit_integrity(' not in s:
    if function_marker not in s:
        raise SystemExit('MCP blackboard_read marker not found')
    fn_text = r'''

async fn blackboard_execution_audit_integrity(
    state: &AppState,
    arguments: &Map<String, Value>,
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
    if let Err(code) = resolve_capability_identity(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_AUDIT,
        Some(&intent_id),
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
'''
    s = s.replace(function_marker, fn_text + function_marker, 1)
p.write_text(s, encoding='utf-8')

# --- HTTP contract test: success and explicit-resource deny on integrity projection ---
p = Path('src/http_contract_tests.rs')
s = p.read_text(encoding='utf-8')
if '/api/executions/intent-http-audit/audit/integrity' not in s:
    success_marker = '    assert_eq!(body["audit"]["ingress"].as_array().unwrap().len(), 1);\n\n'
    if success_marker not in s:
        raise SystemExit('HTTP audit success marker not found')
    success = success_marker + r'''    let integrity = request(
        &audit_router,
        Method::GET,
        "/api/executions/intent-http-audit/audit/integrity",
        Some(&session.token),
        None,
    )
    .await;
    let (integrity_status, integrity_body) = response_json(integrity).await;
    assert_eq!(integrity_status, StatusCode::OK);
    assert_eq!(integrity_body["integrity"]["valid"], true);
    assert!(integrity_body["integrity"]["violations"]
        .as_array()
        .unwrap()
        .is_empty());

'''
    s = s.replace(success_marker, success, 1)
    deny_marker = '    assert_eq!(denied.status(), StatusCode::FORBIDDEN);\n'
    # Limit replacement to the audit test by using the last occurrence.
    idx = s.find('async fn execution_audit_http_is_policy_guarded_and_reads_committed_evidence()')
    pos = s.find(deny_marker, idx)
    if pos < 0:
        raise SystemExit('HTTP audit deny marker not found')
    deny_extra = deny_marker + r'''    let integrity_denied = request(
        &audit_router,
        Method::GET,
        "/api/executions/intent-http-audit/audit/integrity",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(integrity_denied.status(), StatusCode::FORBIDDEN);
'''
    s = s[:pos] + deny_extra + s[pos + len(deny_marker):]
p.write_text(s, encoding='utf-8')

# --- MCP contract tests: tool count + functional canonical verifier projection ---
p = Path('src/mcp_contract_tests.rs')
s = p.read_text(encoding='utf-8')
s = s.replace('assert_eq!(tools.len(), 5);', 'assert_eq!(tools.len(), 6);', 1)
if 'blackboard_execution_audit_integrity' not in s:
    marker = '    assert_eq!(\n        receipt["result"]["structuredContent"]["execution"]["message_id"],\n        message_id\n    );\n'
    if marker not in s:
        raise SystemExit('MCP receipt assertion marker not found')
    extra = marker + r'''

    let integrity = call_tool(
        &fixture.router,
        43,
        "blackboard_execution_audit_integrity",
        capability_arguments(
            &fixture.single_secret,
            "single-main",
            authorization::READ_EXECUTION_AUDIT,
            Some("receipt-001"),
        ),
    )
    .await;
    assert!(!integrity["result"]["isError"].as_bool().unwrap());
    assert_eq!(
        integrity["result"]["structuredContent"]["integrity"]["valid"],
        true
    );
    assert!(integrity["result"]["structuredContent"]["integrity"]["violations"]
        .as_array()
        .unwrap()
        .is_empty());
'''
    s = s.replace(marker, extra, 1)
p.write_text(s, encoding='utf-8')

# --- contract parity tests ---
p = Path('src/contract_parity_tests.rs')
s = p.read_text(encoding='utf-8')
if 'execution_audit_integrity_contracts_match_canonical_rust_shape' not in s:
    s += r'''

#[test]
fn execution_audit_integrity_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert_eq!(
        names(
            &api,
            "/components/schemas/ExecutionAuditIntegrityReport/properties"
        ),
        names(
            &contract_schema::execution_audit_integrity_schema(),
            "/properties"
        )
    );
    assert!(api["paths"]
        .get("/api/executions/{intent_id}/audit/integrity")
        .is_some());
    let utcp = utcp_json();
    assert!(utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .any(|tool| tool["name"] == "execution_audit_integrity"));
}
'''
p.write_text(s, encoding='utf-8')

# --- OpenAPI: path + schemas ---
p = Path('integrations/openapi.yaml')
s = p.read_text(encoding='utf-8')
if '  /api/executions/{intent_id}/audit/integrity:\n' not in s:
    admin_marker = '  /api/admin/channels:\n'
    if admin_marker not in s:
        raise SystemExit('OpenAPI admin path marker not found')
    path_block = r'''  /api/executions/{intent_id}/audit/integrity:
    get:
      operationId: blackboardExecutionAuditIntegrity
      summary: Verify structural integrity of committed execution audit evidence.
      description: Current policy authorizes access; the report is produced by the canonical read-only integrity verifier and does not re-evaluate historical authorization or repair history.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      parameters:
        - name: intent_id
          in: path
          required: true
          schema:
            $ref: '#/components/schemas/IntentId'
      responses:
        '200':
          description: Structural integrity report for the requested semantic execution identity.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ExecutionAuditIntegrityEnvelope'
        '400':
          description: Invalid semantic intent identifier.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'

'''
    s = s.replace(admin_marker, path_block + admin_marker, 1)

if '    ExecutionAuditIntegrityReport:\n' not in s:
    schema_marker = '    Identity:\n'
    if schema_marker not in s:
        raise SystemExit('OpenAPI Identity schema marker not found')
    schema_block = r'''    ExecutionAuditIntegrityReport:
      type: object
      additionalProperties: false
      required: [participant_id, intent_id, valid, checks, violations]
      description: Read-only structural integrity report over committed execution evidence. Historical authorization is not re-evaluated.
      properties:
        participant_id:
          $ref: '#/components/schemas/ParticipantId'
        intent_id:
          $ref: '#/components/schemas/IntentId'
        valid:
          type: boolean
        checks:
          type: array
          items:
            type: string
        violations:
          type: array
          items:
            type: string

    ExecutionAuditIntegrityEnvelope:
      type: object
      additionalProperties: false
      required: [integrity]
      properties:
        integrity:
          $ref: '#/components/schemas/ExecutionAuditIntegrityReport'

'''
    s = s.replace(schema_marker, schema_block + schema_marker, 1)
p.write_text(s, encoding='utf-8')

# --- UTCP: structured append ---
p = Path('integrations/utcp.json')
data = json.loads(p.read_text(encoding='utf-8'))
if not any(tool.get('name') == 'execution_audit_integrity' for tool in data['tools']):
    data['tools'].append({
        'name': 'execution_audit_integrity',
        'description': 'Verify structural consistency of committed execution audit evidence. Current policy controls access; the verifier does not re-evaluate historical authorization or repair history.',
        'inputs': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['intent_id'],
            'properties': {
                'intent_id': {'type': 'string', 'minLength': 1, 'maxLength': 256}
            }
        },
        'outputs': {
            'type': 'object',
            'required': ['integrity'],
            'properties': {
                'integrity': {
                    'type': 'object',
                    'required': ['participant_id', 'intent_id', 'valid', 'checks', 'violations'],
                    'properties': {
                        'participant_id': {'type': 'string'},
                        'intent_id': {'type': 'string'},
                        'valid': {'type': 'boolean'},
                        'checks': {'type': 'array', 'items': {'type': 'string'}},
                        'violations': {'type': 'array', 'items': {'type': 'string'}},
                    }
                }
            }
        },
        'tags': ['blackboard', 'execution', 'audit', 'integrity', 'read'],
        'tool_call_template': {
            'name': 'blackboard_execution_audit_integrity_http',
            'call_template_type': 'http',
            'url': '${BLACKBOARD_URL}/api/executions/${intent_id}/audit/integrity',
            'http_method': 'GET',
            'content_type': 'application/json',
            'body_field': None,
            'auth': {
                'auth_type': 'api_key',
                'api_key': 'Bearer ${BLACKBOARD_TOKEN}',
                'var_name': 'Authorization',
                'location': 'header'
            }
        }
    })
p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
