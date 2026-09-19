from pathlib import Path

# 1) Add a schema-pure replacement for the historical authorize(..., intent_id=None) path.
auth_path = Path('src/authorization.rs')
auth = auth_path.read_text()
anchor = '''pub fn authorize(\n    conn: &Connection,\n    principal: &Principal,\n    participant_id: &str,\n    capability: &str,\n    resource: Option<&str>,\n) -> rusqlite::Result<bool> {\n    Ok(\n        evaluate_authorization(conn, principal, participant_id, capability, resource, None)?\n            .allowed,\n    )\n}\n'''
insert = '''pub fn evaluate_read_authorization(\n    conn: &Connection,\n    principal: &Principal,\n    participant_id: &str,\n    capability: &str,\n    resource: Option<&str>,\n) -> rusqlite::Result<AuthorizationDecision> {\n    // This is the schema-pure equivalent of the historical authorize() read path:\n    // intent_id is deliberately None, so delegated-grant / execution-receipt schema\n    // is not part of the authority decision. Preserve that boundary exactly.\n    if !effective_grants_schema_current(conn)? {\n        return Err(rusqlite::Error::InvalidQuery);\n    }\n    evaluate_authorization_current_schema(\n        conn,\n        principal,\n        participant_id,\n        capability,\n        resource,\n        None,\n    )\n}\n\n'''
if insert not in auth:
    if anchor not in auth:
        raise RuntimeError('authorize anchor not found')
    auth = auth.replace(anchor, insert + anchor, 1)
auth_path.write_text(auth)

api_path = Path('src/access_api.rs')
api = api_path.read_text()

def replace_fn(text: str, name: str, next_name: str, body: str) -> str:
    start_marker = f'async fn {name}('
    end_marker = f'\nasync fn {next_name}('
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f'{name} start not found')
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f'{name} end not found')
    return text[:start] + body.rstrip() + '\n' + text[end+1:]

api = replace_fn(api, 'execution_audit', 'execution_audit_integrity', r'''async fn execution_audit(
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
    let (schema_current, allowed, audit) = with_db_read_only(&state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)? {
            return Ok((false, false, None));
        }
        let decision = authorization::evaluate_read_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )?;
        let audit = if decision.allowed {
            execution::get_execution_audit_bundle(conn, &lookup_participant, &lookup_intent)?
        } else {
            None
        };
        Ok((true, decision.allowed, audit))
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
    }
    let audit =
        audit.ok_or_else(|| AccessApiError::new(StatusCode::NOT_FOUND, "execution_not_found"))?;
    Ok(json_response(StatusCode::OK, json!({"audit": audit})))
}''')

api = replace_fn(api, 'execution_audit_integrity', 'execution_audit_sweep', r'''async fn execution_audit_integrity(
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
    let (schema_current, allowed, report) = with_db_read_only(&state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)? {
            return Ok((false, false, None));
        }
        let decision = authorization::evaluate_read_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )?;
        let report = if decision.allowed {
            Some(execution::verify_execution_audit_integrity(
                conn,
                &lookup_participant,
                &lookup_intent,
            )?)
        } else {
            None
        };
        Ok((true, decision.allowed, report))
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
    }
    let report = report.expect("authorized integrity verification must produce a report");
    Ok(json_response(StatusCode::OK, json!({"integrity": report})))
}''')

api = replace_fn(api, 'execution_audit_sweep', 'authorization_decision_explain', r'''async fn execution_audit_sweep(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (schema_current, allowed, report) = with_db_read_only(&state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)? {
            return Ok((false, false, None));
        }
        let decision = authorization::evaluate_read_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT_SWEEP,
            Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
        )?;
        let report = if decision.allowed {
            Some(execution::sweep_execution_audit_integrity(conn)?)
        } else {
            None
        };
        Ok((true, decision.allowed, report))
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
    }
    let report = report.expect("authorized audit sweep must produce a report");
    Ok(json_response(StatusCode::OK, json!({"sweep": report})))
}

#[derive(Debug, Deserialize)]
struct AuthorizationDecisionExplainQuery {
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
    intent_id: Option<String>,
}''')
# The replacement above includes the next struct because the marker sits after it in source.
# Remove a duplicate struct if the original boundary retained one.
dup = '''#[derive(Debug, Deserialize)]\nstruct AuthorizationDecisionExplainQuery {\n    principal_provider: String,\n    principal_subject: String,\n    participant_id: String,\n    capability: String,\n    resource: Option<String>,\n    intent_id: Option<String>,\n}\n\n#[derive(Debug, Deserialize)]\nstruct AuthorizationDecisionExplainQuery {'''
if dup in api:
    raise RuntimeError('unexpected duplicate decision query struct')

# Policy integrity is followed by principal_for_headers rather than another async fn.
start = api.find('async fn authorization_policy_integrity(')
end = api.find('\nfn principal_for_headers(', start)
if start < 0 or end < 0:
    raise RuntimeError('policy integrity function boundary not found')
policy_integrity = r'''async fn authorization_policy_integrity(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (schema_current, allowed, report) = with_db_read_only(&state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)?
            || !authorization::authorization_integrity_schema_current(conn)?
        {
            return Ok((false, false, None));
        }
        let decision = authorization::evaluate_read_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        )?;
        if !decision.allowed {
            return Ok((true, false, None));
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
    }
    let report =
        report.expect("authorized current-schema policy integrity read must produce report");
    Ok(json_response(StatusCode::OK, json!({"integrity": report})))
}
'''
api = api[:start] + policy_integrity + api[end:]
api_path.write_text(api)

# 3) One stale-schema regression covers all four REST surfaces and proves no repair.
test_path = Path('src/http_contract_tests.rs')
tests = test_path.read_text()
name = 'privileged_audit_rest_reads_refuse_stale_authorization_schema_without_repair'
if name not in tests:
    tests += r'''

#[tokio::test]
async fn privileged_audit_rest_reads_refuse_stale_authorization_schema_without_repair() {
    let fixture = fixture("privileged-read-purity");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let conn = db::connect(&fixture.db_path).unwrap();
    identity::set_web_participant_role(&conn, &fixture.participant_id, "admin").unwrap();
    conn.execute("DROP TABLE principal_grants", []).unwrap();
    let schema_version_before: i64 = conn
        .query_row("PRAGMA schema_version", [], |row| row.get(0))
        .unwrap();
    drop(conn);
    let main_before = std::fs::read(&fixture.db_path).unwrap();
    let session = web_auth::issue_web_session(&fixture.participant_id);

    for uri in [
        "/api/executions/intent-read-purity/audit",
        "/api/executions/intent-read-purity/audit/integrity",
        "/api/execution-audit/sweep",
        "/api/authorization-policy/integrity",
    ] {
        let response = request(&router, Method::GET, uri, Some(&session.token), None).await;
        let (status, body) = response_json(response).await;
        assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE, "{uri}");
        assert_eq!(body["error"], "authorization_schema_not_current", "{uri}");
    }

    let conn = db::connect(&fixture.db_path).unwrap();
    let grant_table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'principal_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(grant_table_count, 0, "privileged REST read repaired grant schema");
    let schema_version_after: i64 = conn
        .query_row("PRAGMA schema_version", [], |row| row.get(0))
        .unwrap();
    assert_eq!(schema_version_after, schema_version_before);
    drop(conn);
    assert_eq!(std::fs::read(&fixture.db_path).unwrap(), main_before);
}
'''
    test_path.write_text(tests)
