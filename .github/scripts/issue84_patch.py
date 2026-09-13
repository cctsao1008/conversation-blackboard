from pathlib import Path
import json

# authorization.rs
p = Path('src/authorization.rs')
s = p.read_text(encoding='utf-8')
s = s.replace(
    'pub const READ_EXECUTION_RECEIPT: &str = "read_execution_receipt";\npub const MANAGE_CHANNELS: &str = "manage_channels";',
    'pub const READ_EXECUTION_RECEIPT: &str = "read_execution_receipt";\npub const READ_EXECUTION_AUDIT: &str = "read_execution_audit";\npub const MANAGE_CHANNELS: &str = "manage_channels";',
    1,
)
s = s.replace('const KNOWN_CAPABILITIES: [&str; 5] = [', 'const KNOWN_CAPABILITIES: [&str; 6] = [', 1)
s = s.replace(
    '    READ_EXECUTION_RECEIPT,\n    MANAGE_CHANNELS,',
    '    READ_EXECUTION_RECEIPT,\n    READ_EXECUTION_AUDIT,\n    MANAGE_CHANNELS,',
    1,
)
s = s.replace(
    'READ_MESSAGES | POST_MESSAGE | REPLY | READ_EXECUTION_RECEIPT',
    'READ_MESSAGES | POST_MESSAGE | REPLY | READ_EXECUTION_RECEIPT | READ_EXECUTION_AUDIT',
)
insert = r'''

    #[test]
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
'''
pos = s.rfind('\n}')
if pos < 0:
    raise SystemExit('authorization test module tail not found')
s = s[:pos] + insert + s[pos:]
p.write_text(s, encoding='utf-8')

# adapter_profile.rs
p = Path('src/adapter_profile.rs')
s = p.read_text(encoding='utf-8')
s = s.replace('        "execution_receipt",\n', '        "execution_receipt",\n        "execution_audit",\n')
p.write_text(s, encoding='utf-8')

# contract_schema.rs
p = Path('src/contract_schema.rs')
s = p.read_text(encoding='utf-8')
append = r'''

pub fn authorization_provenance_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "participant_id": participant_id_schema(),
            "intent_id": intent_id_schema(),
            "source": {"type": "string"},
            "reason": {"type": "string"},
            "grant_id": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
        },
        "required": ["participant_id", "intent_id", "source", "reason", "grant_id"],
        "additionalProperties": false,
        "description": "Historical non-secret authorization decision persisted at execution commit time."
    })
}

pub fn ingress_audit_record_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "delivery_id": delivery_id_schema(),
            "intent_id": intent_id_schema(),
            "transport": {"type": "string"},
            "external_ref": {"type": "string"},
            "principal": principal_schema()
        },
        "required": ["delivery_id", "intent_id", "transport", "external_ref", "principal"],
        "additionalProperties": false
    })
}

pub fn execution_audit_bundle_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "receipt": execution_receipt_schema(),
            "authorization": {
                "anyOf": [authorization_provenance_schema(), {"type": "null"}]
            },
            "ingress": {
                "type": "array",
                "items": ingress_audit_record_schema()
            }
        },
        "required": ["receipt", "authorization", "ingress"],
        "additionalProperties": false,
        "description": "Immutable historical execution read model assembled from committed receipt, authorization provenance, and ingress provenance."
    })
}

pub fn execution_audit_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"audit": execution_audit_bundle_schema()},
        "required": ["audit"],
        "additionalProperties": false
    })
}
'''
if 'pub fn execution_audit_bundle_schema()' not in s:
    s = s.rstrip() + append + '\n'
p.write_text(s, encoding='utf-8')

# access_api.rs
p = Path('src/access_api.rs')
s = p.read_text(encoding='utf-8')
s = s.replace(
    '        .route("/api/executions/{intent_id}", get(execution_receipt))\n',
    '        .route("/api/executions/{intent_id}", get(execution_receipt))\n        .route("/api/executions/{intent_id}/audit", get(execution_audit))\n',
    1,
)
marker = 'fn principal_for_headers(headers: &HeaderMap, resolved: &Identity) -> execution::Principal {'
fn = r'''async fn execution_audit(
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
    let (allowed, audit) = with_db(&state, move |conn| {
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )?;
        let audit = if allowed {
            execution::get_execution_audit_bundle(conn, &lookup_participant, &lookup_intent)?
        } else {
            None
        };
        Ok((allowed, audit))
    })
    .await?;
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    let audit = audit
        .ok_or_else(|| AccessApiError::new(StatusCode::NOT_FOUND, "execution_not_found"))?;
    Ok(json_response(StatusCode::OK, json!({"audit": audit})))
}

'''
if marker not in s:
    raise SystemExit('access_api principal marker not found')
if 'async fn execution_audit(' not in s:
    s = s.replace(marker, fn + marker, 1)
p.write_text(s, encoding='utf-8')

# mcp.rs
p = Path('src/mcp.rs')
s = p.read_text(encoding='utf-8')
s = s.replace(
    'and blackboard_execution_receipt for semantic execution read-back.',
    'blackboard_execution_receipt for semantic execution read-back, and blackboard_execution_audit for immutable historical audit.',
    1,
)
needle = r'''            {
                "name": "blackboard_execution_receipt",
                "title": "Read Blackboard Execution Receipt",
                "description": "Read back the durable semantic execution receipt for one authenticated participant intent.",
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
                "outputSchema": contract_schema::execution_receipt_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            }
'''
replacement = needle[:-2] + r''',
            {
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
if needle not in s:
    raise SystemExit('MCP receipt tool block not found')
s = s.replace(needle, replacement, 1)
s = s.replace(
    '        "blackboard_execution_receipt" => Ok(blackboard_execution_receipt(state, &arguments).await),\n',
    '        "blackboard_execution_receipt" => Ok(blackboard_execution_receipt(state, &arguments).await),\n        "blackboard_execution_audit" => Ok(blackboard_execution_audit(state, &arguments).await),\n',
    1,
)
marker = 'async fn blackboard_read(state: &AppState, arguments: &Map<String, Value>) -> Value {'
fn = r'''async fn blackboard_execution_audit(state: &AppState, arguments: &Map<String, Value>) -> Value {
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
    let audit = match with_db(state, move |conn| {
        if !authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )? {
            return Ok(None);
        }
        execution::get_execution_audit_bundle(conn, &lookup_participant, &lookup_intent)
    })
    .await
    {
        Ok(Some(audit)) => audit,
        Ok(None) => return tool_error("execution_not_found"),
        Err(()) => return tool_error("database_unavailable"),
    };
    tool_success(json!({"audit": audit}))
}

'''
if marker not in s:
    raise SystemExit('MCP blackboard_read marker not found')
if 'async fn blackboard_execution_audit(' not in s:
    s = s.replace(marker, fn + marker, 1)
p.write_text(s, encoding='utf-8')

# OpenAPI
p = Path('integrations/openapi.yaml')
s = p.read_text(encoding='utf-8')
path_block = r'''  /api/executions/{intent_id}/audit:
    get:
      operationId: blackboardExecutionAudit
      summary: Read immutable committed execution audit evidence.
      description: Current policy authorizes access to this historical read model; historical authorization contents are read from persisted evidence and are not re-evaluated.
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
          description: Immutable historical execution audit bundle.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ExecutionAuditEnvelope'
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
        '404':
          description: No committed execution exists for this participant and intent.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

'''
if '/api/executions/{intent_id}/audit:' not in s:
    marker = '  /api/admin/channels:\n'
    if marker not in s:
        raise SystemExit('OpenAPI admin marker not found')
    s = s.replace(marker, path_block + marker, 1)
schema_block = r'''    AuthorizationProvenance:
      type: object
      additionalProperties: false
      required: [participant_id, intent_id, source, reason, grant_id]
      description: Historical non-secret authorization decision persisted at execution commit time.
      properties:
        participant_id:
          $ref: '#/components/schemas/ParticipantId'
        intent_id:
          $ref: '#/components/schemas/IntentId'
        source:
          type: string
        reason:
          type: string
        grant_id:
          type: [integer, 'null']
          minimum: 1

    IngressAuditRecord:
      type: object
      additionalProperties: false
      required: [delivery_id, intent_id, transport, external_ref, principal]
      properties:
        delivery_id:
          $ref: '#/components/schemas/DeliveryId'
        intent_id:
          $ref: '#/components/schemas/IntentId'
        transport:
          type: string
        external_ref:
          type: string
        principal:
          $ref: '#/components/schemas/Principal'

    ExecutionAuditBundle:
      type: object
      additionalProperties: false
      required: [receipt, authorization, ingress]
      description: Immutable historical read model assembled from committed execution evidence.
      properties:
        receipt:
          $ref: '#/components/schemas/ExecutionReceipt'
        authorization:
          anyOf:
            - $ref: '#/components/schemas/AuthorizationProvenance'
            - type: 'null'
        ingress:
          type: array
          items:
            $ref: '#/components/schemas/IngressAuditRecord'

    ExecutionAuditEnvelope:
      type: object
      additionalProperties: false
      required: [audit]
      properties:
        audit:
          $ref: '#/components/schemas/ExecutionAuditBundle'

'''
if '    ExecutionAuditBundle:\n' not in s:
    marker = '    Identity:\n'
    if marker not in s:
        raise SystemExit('OpenAPI Identity schema marker not found')
    s = s.replace(marker, schema_block + marker, 1)
p.write_text(s, encoding='utf-8')

# UTCP
p = Path('integrations/utcp.json')
data = json.loads(p.read_text(encoding='utf-8'))
if not any(tool.get('name') == 'execution_audit' for tool in data['tools']):
    data['tools'].append({
        'name': 'execution_audit',
        'description': 'Read immutable committed execution audit evidence. Current policy controls access; historical authorization is not re-evaluated.',
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
            'required': ['audit'],
            'properties': {
                'audit': {
                    'type': 'object',
                    'required': ['receipt', 'authorization', 'ingress'],
                    'properties': {
                        'receipt': {'type': 'object'},
                        'authorization': {'type': ['object', 'null']},
                        'ingress': {'type': 'array', 'items': {'type': 'object'}}
                    }
                }
            }
        },
        'tags': ['blackboard', 'execution', 'audit', 'read'],
        'tool_call_template': {
            'name': 'blackboard_execution_audit_http',
            'call_template_type': 'http',
            'url': '${BLACKBOARD_URL}/api/executions/${intent_id}/audit',
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
p.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')

# contract parity tests
p = Path('src/contract_parity_tests.rs')
s = p.read_text(encoding='utf-8')
s = s.replace(
    '    assert!(tools.iter().any(|tool| tool["name"] == "execution_receipt"));\n',
    '    assert!(tools.iter().any(|tool| tool["name"] == "execution_receipt"));\n    assert!(tools.iter().any(|tool| tool["name"] == "execution_audit"));\n',
    1,
)
extra = r'''

#[test]
fn execution_audit_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert_eq!(
        names(&api, "/components/schemas/ExecutionAuditBundle/properties"),
        names(&contract_schema::execution_audit_bundle_schema(), "/properties")
    );
    assert_eq!(
        names(&api, "/components/schemas/AuthorizationProvenance/properties"),
        names(&contract_schema::authorization_provenance_schema(), "/properties")
    );
    assert_eq!(
        names(&api, "/components/schemas/IngressAuditRecord/properties"),
        names(&contract_schema::ingress_audit_record_schema(), "/properties")
    );
    assert!(api["paths"].get("/api/executions/{intent_id}/audit").is_some());
    let audit = contract_schema::execution_audit_bundle_schema();
    assert!(audit["properties"]["receipt"]["properties"].get("delivery_id").is_none());
    assert!(audit["properties"]["ingress"]["items"]["properties"]
        .get("delivery_id")
        .is_some());
}
'''
if 'fn execution_audit_contracts_match_canonical_rust_shape()' not in s:
    s = s.rstrip() + extra + '\n'
p.write_text(s, encoding='utf-8')

# HTTP contract test: current read authorization controls access, persisted bundle remains historical.
p = Path('src/http_contract_tests.rs')
s = p.read_text(encoding='utf-8')
s = s.replace(
    'use crate::{\n    db,\n',
    'use crate::{\n    authorization, db, execution,\n',
    1,
)
extra = r'''

#[tokio::test]
async fn execution_audit_http_is_policy_guarded_and_reads_committed_evidence() {
    let fixture = fixture("audit");
    let conn = db::connect(&fixture.db_path).unwrap();
    execution::ensure_execution_tables(&conn).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO execution_receipts
            (participant_id, intent_id, intent_hash, capability, message_id, status)
         VALUES (?1, 'intent-http-audit', 'hash', 'post_message', 1, 'committed')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, source, reason, grant_id)
         VALUES (?1, 'intent-http-audit', 'implicit_authority', 'implicit_human_web', NULL)",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO ingress_provenance
            (delivery_id, intent_id, transport, external_ref, principal_provider, principal_subject)
         VALUES ('delivery-http-audit', 'intent-http-audit', 'rest', 'ref', 'human-web', ?1)",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);

    let session = web_auth::issue_web_session(&fixture.participant_id);
    let response = request(
        &fixture.router,
        Method::GET,
        "/api/executions/intent-http-audit/audit",
        Some(&session.token),
        None,
    )
    .await;
    let (status, body) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["audit"]["receipt"]["intent_id"], "intent-http-audit");
    assert_eq!(body["audit"]["authorization"]["reason"], "implicit_human_web");
    assert_eq!(body["audit"]["ingress"].as_array().unwrap().len(), 1);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('human-web', ?1, ?1, 'read_execution_audit', 'different-intent')",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);
    let denied = request(
        &fixture.router,
        Method::GET,
        "/api/executions/intent-http-audit/audit",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);
}
'''
if 'fn execution_audit_http_is_policy_guarded_and_reads_committed_evidence()' not in s:
    s = s.rstrip() + extra + '\n'
p.write_text(s, encoding='utf-8')
