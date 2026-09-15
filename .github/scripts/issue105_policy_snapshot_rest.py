from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# REST route + handler
p = Path("src/access_api.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''        .route(\n            "/api/authorization-policy/integrity",\n            get(authorization_policy_integrity),\n        )''',
    '''        .route("/api/authorization-policy", get(authorization_policy))\n        .route(\n            "/api/authorization-policy/integrity",\n            get(authorization_policy_integrity),\n        )''',
    "authorization policy route",
)

handler_anchor = '''async fn authorization_policy_integrity(\n    State(state): State<AppState>,'''
handler = r'''async fn authorization_policy(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (allowed, schema_current, snapshot) = with_db(&state, move |conn| {
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
            return Ok((false, true, None));
        }
        Ok((
            true,
            true,
            Some(authorization::read_authorization_policy_snapshot(conn)?),
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
    let snapshot = snapshot.expect("authorized current-schema policy read must produce snapshot");
    Ok(json_response(StatusCode::OK, json!({"policy": snapshot})))
}

'''
s = replace_once(s, handler_anchor, handler + handler_anchor, "authorization policy handler")
p.write_text(s, encoding="utf-8")

# HTTP behavioral regressions
p = Path("src/http_contract_tests.rs")
s = p.read_text(encoding="utf-8")
test_anchor = '''#[tokio::test]\nasync fn authorization_policy_integrity_http_is_privileged_read_only_and_reports_invalid_state() {'''
test = r'''#[tokio::test]
async fn authorization_policy_http_is_privileged_canonical_and_observationally_read_only() {
    let fixture = fixture("policy-snapshot");
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
        "/api/authorization-policy",
        None,
        None,
    )
    .await;
    assert_eq!(unauthenticated.status(), StatusCode::UNAUTHORIZED);

    let session = web_auth::issue_web_session(&fixture.participant_id);
    let ordinary = request(
        &router,
        Method::GET,
        "/api/authorization-policy",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    // Policy-integrity authority is deliberately separate from policy inventory.
    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('human-web', ?1, ?1, 'read_authorization_policy_integrity',
                 'authorization-policy-integrity')",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);
    let wrong_capability = request(
        &router,
        Method::GET,
        "/api/authorization-policy",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(wrong_capability.status(), StatusCode::FORBIDDEN);

    // Seed inventory state that must be projected without filtering or repair.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource, status)
         VALUES ('oidc:https://issuer.example', 'active-agent', ?1,
                 'post_message', 'alpha', 'active')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource, status)
         VALUES ('oidc:https://issuer.example', 'inactive-agent', ?1,
                 'read_messages', NULL, 'inactive')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource,
             intent_id, expires_at, one_shot, status)
         VALUES ('oidc:https://issuer.example', 'expired-agent', ?1, 'post_message',
                 'beta', 'expired-http-intent', unixepoch() - 60, 0, 'active')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource,
             intent_id, one_shot, consumed_at, consumed_intent_id, status)
         VALUES ('oidc:https://issuer.example', 'consumed-agent', ?1, 'post_message',
                 'gamma', 'consumed-http-intent', 1, unixepoch(),
                 'consumed-http-intent', 'inactive')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "UPDATE web_participants SET role = 'admin' WHERE participant_id = ?1",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);

    let response = request(
        &router,
        Method::GET,
        "/api/authorization-policy",
        Some(&session.token),
        None,
    )
    .await;
    let (status, body) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    let durable = body["policy"]["durable_grants"].as_array().unwrap();
    assert!(durable.iter().any(|entry| {
        entry["principal_subject"] == "active-agent" && entry["status"] == "active"
    }));
    assert!(durable.iter().any(|entry| {
        entry["principal_subject"] == "inactive-agent" && entry["status"] == "inactive"
    }));
    assert!(durable
        .iter()
        .all(|entry| entry.get("created_at").is_some() && entry.get("updated_at").is_some()));

    let delegated = body["policy"]["delegated_grants"].as_array().unwrap();
    assert!(delegated.iter().any(|entry| {
        entry["intent_id"] == "expired-http-intent" && entry["expires_at"].is_number()
    }));
    assert!(delegated.iter().any(|entry| {
        entry["intent_id"] == "consumed-http-intent"
            && entry["one_shot"] == true
            && entry["consumed_at"].is_number()
            && entry["consumed_intent_id"] == "consumed-http-intent"
            && entry["status"] == "inactive"
    }));
    assert!(delegated
        .iter()
        .all(|entry| entry.get("created_at").is_none() && entry.get("updated_at").is_none()));
    let serialized = body.to_string();
    assert!(!serialized.contains(&fixture.signing_private));

    // Legacy schema must fail before authorize() compatibility logic can mutate it.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    drop(conn);
    let legacy = request(
        &router,
        Method::GET,
        "/api/authorization-policy",
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
    assert_eq!(after, 0, "read-only policy GET recreated authorization schema");
}

'''
s = replace_once(s, test_anchor, test + test_anchor, "authorization policy HTTP regressions")
p.write_text(s, encoding="utf-8")
