from pathlib import Path
import json


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# REST: schema-current gate must run before authorization because authorize()
# contains compatibility schema ensure logic. A read-only integrity endpoint must
# never migrate an old authorization schema while trying to authorize the read.
p = Path("src/access_api.rs")
s = p.read_text(encoding="utf-8")
old = '''    let (allowed, schema_current, report) = with_db(&state, move |conn| {
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
    }'''
new = '''    let (allowed, schema_current, report) = with_db(&state, move |conn| {
        let schema_current = authorization::authorization_integrity_schema_current(conn)?;
        if !schema_current {
            return Ok((false, false, None));
        }
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
        Ok((
            true,
            true,
            Some(authorization::audit_authorization_integrity(conn)?),
        ))
    })
    .await?;
    if !schema_current {
        return Err(AccessApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "authorization_schema_not_current",
        ));
    }
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }'''
s = replace_once(s, old, new, "REST policy integrity schema gate ordering")
p.write_text(s, encoding="utf-8")

# MCP: same read-only gate ordering.
p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")
old = '''    let (schema_current, report) = match with_db(state, move |conn| {
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
    })'''
new = '''    let (schema_current, report) = match with_db(state, move |conn| {
        let schema_current = authorization::authorization_integrity_schema_current(conn)?;
        if !schema_current {
            return Ok((false, None));
        }
        if !authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        )? {
            return Ok((true, None));
        }
        Ok((
            true,
            Some(authorization::audit_authorization_integrity(conn)?),
        ))
    })'''
s = replace_once(s, old, new, "MCP policy integrity schema gate ordering")
p.write_text(s, encoding="utf-8")

# REST behavior matrix and no-migration regression.
p = Path("src/http_contract_tests.rs")
s = p.read_text(encoding="utf-8")
if "authorization_policy_integrity_http_is_privileged_read_only_and_reports_invalid_state" in s:
    raise SystemExit("REST policy integrity regression already present")
s += r'''

#[tokio::test]
async fn authorization_policy_integrity_http_is_privileged_read_only_and_reports_invalid_state() {
    let fixture = fixture("policy-integrity");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));

    let unauthenticated = request(
        &router,
        Method::GET,
        "/api/authorization-policy/integrity",
        None,
        None,
    )
    .await;
    assert_eq!(unauthenticated.status(), StatusCode::UNAUTHORIZED);

    let session = web_auth::issue_web_session(&fixture.participant_id);
    let ordinary = request(
        &router,
        Method::GET,
        "/api/authorization-policy/integrity",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    // A different privileged capability must not imply policy-integrity access.
    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('human-web', ?1, ?1, 'read_execution_audit_sweep', 'execution-audit-sweep')",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);
    let wrong_capability = request(
        &router,
        Method::GET,
        "/api/authorization-policy/integrity",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(wrong_capability.status(), StatusCode::FORBIDDEN);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "UPDATE web_participants SET role = 'admin' WHERE participant_id = ?1",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);

    let clean = request(
        &router,
        Method::GET,
        "/api/authorization-policy/integrity",
        Some(&session.token),
        None,
    )
    .await;
    let (clean_status, clean_body) = response_json(clean).await;
    assert_eq!(clean_status, StatusCode::OK);
    assert_eq!(clean_body["integrity"]["valid"], true);

    // Invalid policy is report data, not an HTTP failure and not auto-repaired.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('human-web', ?1, ?1, 'definitely_unknown_capability', NULL)",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);
    let invalid = request(
        &router,
        Method::GET,
        "/api/authorization-policy/integrity",
        Some(&session.token),
        None,
    )
    .await;
    let (invalid_status, invalid_body) = response_json(invalid).await;
    assert_eq!(invalid_status, StatusCode::OK);
    assert_eq!(invalid_body["integrity"]["valid"], false);
    assert!(invalid_body["integrity"]["violations"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| entry["kind"] == "unsupported_capability"));

    // Legacy schema must fail before authorize() can run its compatibility
    // ensure logic. The missing table must remain missing after the GET.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    let before: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'delegated_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(before, 0);
    drop(conn);

    let legacy = request(
        &router,
        Method::GET,
        "/api/authorization-policy/integrity",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(legacy.status(), StatusCode::SERVICE_UNAVAILABLE);

    let conn = db::connect(&fixture.db_path).unwrap();
    let after: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'delegated_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(after, 0, "read-only integrity GET recreated authorization schema");
}
'''
p.write_text(s, encoding="utf-8")

# MCP HMAC and OIDC behavior matrix plus no-migration regression.
p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
anchor = '''#[tokio::test]
async fn mcp_audit_sweep_is_privileged_and_projects_canonical_report() {'''
helper = r'''fn policy_integrity_arguments(secret: &str, participant_id: &str) -> Value {
    let proof = participant_auth::compute_capability_proof(
        secret,
        participant_id,
        authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
        Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
    )
    .unwrap();
    json!({
        "participant_id": participant_id,
        "auth": {"scheme": participant_auth::AUTH_SCHEME, "proof": proof}
    })
}

''' + anchor
s = replace_once(s, anchor, helper, "policy integrity HMAC argument helper")

anchor = '''#[tokio::test]
async fn bearer_audit_sweep_remains_explicitly_grant_controlled() {'''
tests = r'''#[tokio::test]
async fn mcp_policy_integrity_requires_its_own_explicit_hmac_grant_and_never_migrates_schema() {
    let fixture = fixture();

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

    let denied = call_tool(
        &fixture.router,
        117,
        "blackboard_authorization_policy_integrity",
        policy_integrity_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    assert_eq!(tool_error_code(&denied), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main',
                 'read_authorization_policy_integrity', 'authorization-policy-integrity')",
        [],
    )
    .unwrap();
    drop(conn);

    let clean = call_tool(
        &fixture.router,
        118,
        "blackboard_authorization_policy_integrity",
        policy_integrity_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    assert_eq!(clean["result"]["isError"], false);
    assert_eq!(clean["result"]["structuredContent"]["integrity"]["valid"], true);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    drop(conn);

    let legacy = call_tool(
        &fixture.router,
        119,
        "blackboard_authorization_policy_integrity",
        policy_integrity_arguments(&fixture.single_secret, "single-main"),
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
    assert_eq!(table_count, 0, "MCP integrity read recreated authorization schema");
}

#[tokio::test]
async fn bearer_policy_integrity_requires_explicit_blackboard_grant() {
    let fixture = fixture();
    let (verifier, token) = bearer_verifier_and_token("policy-auditor");
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
            "id": 120,
            "method": "tools/call",
            "params": {
                "name": "blackboard_authorization_policy_integrity",
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
         VALUES ('oidc:https://issuer.example', 'policy-auditor', 'single-main',
                 'read_authorization_policy_integrity', 'authorization-policy-integrity')",
        [],
    )
    .unwrap();
    drop(conn);

    let allowed = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 121,
            "method": "tools/call",
            "params": {
                "name": "blackboard_authorization_policy_integrity",
                "arguments": {"participant_id": "single-main"}
            }
        }),
        &authorization_header,
    )
    .await;
    let (status, allowed_value) = response_json(allowed).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(allowed_value["result"]["isError"], false);
    assert_eq!(
        allowed_value["result"]["structuredContent"]["integrity"]["valid"],
        true
    );
}

''' + anchor
s = replace_once(s, anchor, tests, "policy integrity MCP behavior regressions")
p.write_text(s, encoding="utf-8")

# OpenAPI path projection.
p = Path("integrations/openapi.yaml")
s = p.read_text(encoding="utf-8")
anchor = '''  /api/admin/channels:
'''
path_block = '''  /api/authorization-policy/integrity:
    get:
      operationId: blackboardAuthorizationPolicyIntegrity
      summary: Verify integrity of durable authorization policy objects.
      description: Privileged projection of the canonical read-only authorization-policy integrity audit. Invalid policy state is returned as report data with HTTP 200; legacy authorization schemas fail without migration or repair.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      responses:
        '200':
          description: Canonical authorization-policy integrity report.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationIntegrityEnvelope'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '503':
          description: Authorization schema is not current. The read does not migrate or repair it.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

''' + anchor
s = replace_once(s, anchor, path_block, "OpenAPI policy integrity path")

anchor = '''    Identity:
'''
schema_block = '''    AuthorizationIntegrityViolation:
      type: object
      additionalProperties: false
      required: [kind, store, grant_id, participant_id, detail]
      description: Non-secret read-only classification of malformed or ambiguous authorization-policy state.
      properties:
        kind:
          type: string
        store:
          type: string
        grant_id:
          type: [integer, 'null']
          minimum: 1
        participant_id:
          anyOf:
            - $ref: '#/components/schemas/ParticipantId'
            - type: 'null'
        detail:
          type: string

    AuthorizationIntegrityReport:
      type: object
      additionalProperties: false
      required: [valid, durable_grants_scanned, delegated_grants_scanned, violations]
      description: Canonical read-only integrity report over Blackboard authorization policy objects.
      properties:
        valid:
          type: boolean
        durable_grants_scanned:
          type: integer
          minimum: 0
        delegated_grants_scanned:
          type: integer
          minimum: 0
        violations:
          type: array
          items:
            $ref: '#/components/schemas/AuthorizationIntegrityViolation'

    AuthorizationIntegrityEnvelope:
      type: object
      additionalProperties: false
      required: [integrity]
      properties:
        integrity:
          $ref: '#/components/schemas/AuthorizationIntegrityReport'

''' + anchor
s = replace_once(s, anchor, schema_block, "OpenAPI policy integrity schemas")
p.write_text(s, encoding="utf-8")

# UTCP is discovery projection over the REST endpoint; keep authorization in Blackboard.
p = Path("integrations/utcp.json")
utcp = json.loads(p.read_text(encoding="utf-8"))
if any(tool.get("name") == "authorization_policy_integrity" for tool in utcp["tools"]):
    raise SystemExit("UTCP policy integrity tool already exists")
utcp["tools"].append({
    "name": "authorization_policy_integrity",
    "description": "Run the privileged read-only authorization-policy integrity audit. Blackboard authorization remains authoritative; invalid policy state is returned as report data and the read never migrates or repairs policy storage.",
    "inputs": {
        "type": "object",
        "additionalProperties": False,
        "properties": {}
    },
    "outputs": {
        "type": "object",
        "required": ["integrity"],
        "properties": {
            "integrity": {
                "type": "object",
                "additionalProperties": False,
                "required": ["valid", "durable_grants_scanned", "delegated_grants_scanned", "violations"],
                "properties": {
                    "valid": {"type": "boolean"},
                    "durable_grants_scanned": {"type": "integer", "minimum": 0},
                    "delegated_grants_scanned": {"type": "integer", "minimum": 0},
                    "violations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["kind", "store", "grant_id", "participant_id", "detail"],
                            "properties": {
                                "kind": {"type": "string"},
                                "store": {"type": "string"},
                                "grant_id": {"type": ["integer", "null"], "minimum": 1},
                                "participant_id": {"type": ["string", "null"], "maxLength": 64},
                                "detail": {"type": "string"}
                            }
                        }
                    }
                }
            }
        }
    },
    "tags": ["blackboard", "authorization", "integrity", "privileged", "read"],
    "tool_call_template": {
        "name": "blackboard_authorization_policy_integrity_http",
        "call_template_type": "http",
        "url": "${BLACKBOARD_URL}/api/authorization-policy/integrity",
        "http_method": "GET",
        "content_type": "application/json",
        "body_field": None,
        "auth": {
            "auth_type": "api_key",
            "api_key": "Bearer ${BLACKBOARD_TOKEN}",
            "var_name": "Authorization",
            "location": "header"
        }
    }
})
p.write_text(json.dumps(utcp, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Cross-projection parity against canonical Rust schema.
p = Path("src/contract_parity_tests.rs")
s = p.read_text(encoding="utf-8")
if "authorization_policy_integrity_contracts_match_canonical_rust_shape" in s:
    raise SystemExit("policy integrity parity test already exists")
s += r'''

#[test]
fn authorization_policy_integrity_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert!(api["paths"]
        .get("/api/authorization-policy/integrity")
        .is_some());
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationIntegrityReport/properties"
        ),
        names(&contract_schema::authorization_integrity_schema(), "/properties")
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationIntegrityViolation/properties"
        ),
        names(
            &contract_schema::authorization_integrity_violation_schema(),
            "/properties"
        )
    );

    let utcp = utcp_json();
    let policy = utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "authorization_policy_integrity")
        .expect("UTCP authorization-policy integrity projection must exist");
    assert_eq!(
        names(policy, "/outputs/properties/integrity/properties"),
        names(&contract_schema::authorization_integrity_schema(), "/properties")
    );
    assert_eq!(
        names(
            policy,
            "/outputs/properties/integrity/properties/violations/items/properties"
        ),
        names(
            &contract_schema::authorization_integrity_violation_schema(),
            "/properties"
        )
    );
    assert_eq!(
        policy["tool_call_template"]["url"],
        "${BLACKBOARD_URL}/api/authorization-policy/integrity"
    );
}
'''
p.write_text(s, encoding="utf-8")
