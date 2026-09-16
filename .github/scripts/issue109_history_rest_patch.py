from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

# authorization capability + implicit authority
auth_path = Path("src/authorization.rs")
auth = auth_path.read_text()
auth = replace_once(
    auth,
    'pub const MANAGE_AUTHORIZATION_POLICY: &str = "manage_authorization_policy";\npub const AUTHORIZATION_POLICY_ADMIN_RESOURCE: &str = "authorization-policy-administration";\npub const MANAGE_CHANNELS: &str = "manage_channels";\n\nconst KNOWN_CAPABILITIES: [&str; 11] = [',
    'pub const MANAGE_AUTHORIZATION_POLICY: &str = "manage_authorization_policy";\npub const AUTHORIZATION_POLICY_ADMIN_RESOURCE: &str = "authorization-policy-administration";\npub const READ_AUTHORIZATION_ADMINISTRATION_HISTORY: &str =\n    "read_authorization_administration_history";\npub const AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE: &str =\n    "authorization-administration-history";\npub const MANAGE_CHANNELS: &str = "manage_channels";\n\nconst KNOWN_CAPABILITIES: [&str; 12] = [',
    "authorization constants",
)
auth = replace_once(
    auth,
    '    READ_AUTHORIZATION_DECISION,\n    MANAGE_AUTHORIZATION_POLICY,\n    MANAGE_CHANNELS,',
    '    READ_AUTHORIZATION_DECISION,\n    MANAGE_AUTHORIZATION_POLICY,\n    READ_AUTHORIZATION_ADMINISTRATION_HISTORY,\n    MANAGE_CHANNELS,',
    "known capabilities",
)
auth = replace_once(
    auth,
    '        if capability == MANAGE_AUTHORIZATION_POLICY {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE))\n            .then_some("implicit_human_web_admin_authorization_administration");\n        }\n        if capability == READ_EXECUTION_AUDIT_SWEEP {',
    '        if capability == MANAGE_AUTHORIZATION_POLICY {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE))\n            .then_some("implicit_human_web_admin_authorization_administration");\n        }\n        if capability == READ_AUTHORIZATION_ADMINISTRATION_HISTORY {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE))\n            .then_some("implicit_human_web_admin_authorization_administration_history");\n        }\n        if capability == READ_EXECUTION_AUDIT_SWEEP {',
    "implicit history authority",
)
auth_path.write_text(auth)

# administration event becomes an external read model
admin_path = Path("src/authorization_admin.rs")
admin = admin_path.read_text()
admin = replace_once(
    admin,
    'use rusqlite::{params, Connection, OptionalExtension, Transaction};\n\nuse crate::{authorization, execution, execution::Principal, identity};',
    'use rusqlite::{params, Connection, OptionalExtension, Transaction};\nuse serde::Serialize;\n\nuse crate::{authorization, execution, execution::Principal, identity};',
    "serde import",
)
admin = replace_once(
    admin,
    '#[derive(Debug, Clone, PartialEq, Eq)]\npub struct AuthorizationAdministrationEvent {',
    '#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct AuthorizationAdministrationEvent {',
    "event serialize derive",
)
admin_path.write_text(admin)

# REST projection
api_path = Path("src/access_api.rs")
api = api_path.read_text()
api = replace_once(
    api,
    '        .route(\n            "/api/authorization-decision/explain",\n            get(authorization_decision_explain),\n        )\n        .route(\n            "/api/authorization-grants/durable",',
    '        .route(\n            "/api/authorization-decision/explain",\n            get(authorization_decision_explain),\n        )\n        .route(\n            "/api/authorization-administration/history",\n            get(authorization_administration_history),\n        )\n        .route(\n            "/api/authorization-grants/durable",',
    "history route",
)
handler = r'''
#[derive(Debug, Deserialize)]
struct AuthorizationAdministrationHistoryQuery {
    participant_id: Option<String>,
}

async fn authorization_administration_history(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<AuthorizationAdministrationHistoryQuery>,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let caller_participant_id = resolved.instance.clone();
    let participant_filter = query
        .participant_id
        .map(|value| {
            identity::validate_participant_id(&value).ok_or_else(|| {
                AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_participant_id")
            })
        })
        .transpose()?;

    let (schema_current, allowed, events) = with_db_read_only(&state, move |conn| {
        if !authorization_admin::schema_current(conn)? {
            return Ok((false, false, Vec::new()));
        }
        let decision = authorization::explain_authorization(
            conn,
            &principal,
            &caller_participant_id,
            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
            None,
        )?;
        let events = if decision.allowed {
            authorization_admin::read_administration_events(conn, participant_filter.as_deref())?
        } else {
            Vec::new()
        };
        Ok((true, decision.allowed, events))
    })
    .await?;

    if !schema_current {
        return Err(AccessApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "schema_migration_required",
        ));
    }
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }

    Ok(json_response(
        StatusCode::OK,
        json!({"history": {"events": events}}),
    ))
}

'''
api = replace_once(
    api,
    '#[derive(Debug, Deserialize)]\nstruct DurableGrantCreateBody {',
    handler + '#[derive(Debug, Deserialize)]\nstruct DurableGrantCreateBody {',
    "history handler",
)
api_path.write_text(api)

# REST regressions
test_path = Path("src/http_contract_tests.rs")
tests = test_path.read_text()
append = r'''

#[tokio::test]
async fn authorization_administration_history_rest_requires_dedicated_authority_and_is_read_only() {
    let fixture = fixture("authorization-admin-history-http");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let uri = "/api/authorization-administration/history";

    let conn = db::connect(&fixture.db_path).unwrap();
    let actor = crate::authorization_admin::AuthorizationAdministrationActor::local_cli();
    let spec = crate::authorization_admin::DurableGrantCreateRequest {
        principal_provider: "oidc:https://issuer.example",
        principal_subject: "history-worker",
        participant_id: &fixture.participant_id,
        capability: authorization::READ_MESSAGES,
        resource: None,
    };
    let created = crate::authorization_admin::create_durable_grant(&conn, &actor, &spec).unwrap();
    let before_events: i64 = conn
        .query_row("SELECT COUNT(*) FROM authorization_admin_events", [], |row| row.get(0))
        .unwrap();
    drop(conn);

    let session = web_auth::issue_web_session(&fixture.participant_id);
    let ordinary = request(&router, Method::GET, uri, Some(&session.token), None).await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    let canonical = web_auth::canonical_http_request_bytes(&fixture.participant_id, "GET", uri);
    let proof = participant_auth::compute_message_proof(&fixture.signing_private, &canonical).unwrap();
    let hmac_denied = router
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::GET)
                .uri(uri)
                .header(request_auth::PARTICIPANT_ID_HEADER, &fixture.participant_id)
                .header(request_auth::AUTH_SCHEME_HEADER, participant_auth::AUTH_SCHEME)
                .header(request_auth::AUTH_PROOF_HEADER, proof.clone())
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(hmac_denied.status(), StatusCode::FORBIDDEN);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', ?1, ?1, ?2, ?3)",
        rusqlite::params![
            &fixture.participant_id,
            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE,
        ],
    )
    .unwrap();
    drop(conn);

    let hmac_allowed = router
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::GET)
                .uri(uri)
                .header(request_auth::PARTICIPANT_ID_HEADER, &fixture.participant_id)
                .header(request_auth::AUTH_SCHEME_HEADER, participant_auth::AUTH_SCHEME)
                .header(request_auth::AUTH_PROOF_HEADER, proof)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let (hmac_status, hmac_body) = response_json(hmac_allowed).await;
    assert_eq!(hmac_status, StatusCode::OK);
    assert_eq!(hmac_body["history"]["events"].as_array().unwrap().len(), 1);
    assert_eq!(hmac_body["history"]["events"][0]["grant_id"], created.id);

    let conn = db::connect(&fixture.db_path).unwrap();
    identity::set_web_participant_role(&conn, &fixture.participant_id, "admin").unwrap();
    drop(conn);
    let admin = request(&router, Method::GET, uri, Some(&session.token), None).await;
    let (admin_status, admin_body) = response_json(admin).await;
    assert_eq!(admin_status, StatusCode::OK);
    assert_eq!(admin_body["history"]["events"][0]["operation"], "create");
    assert_eq!(admin_body["history"]["events"][0]["actor_surface"], "local-cli");

    let filtered_uri = format!(
        "/api/authorization-administration/history?participant_id={}",
        fixture.participant_id
    );
    let filtered = request(
        &router,
        Method::GET,
        &filtered_uri,
        Some(&session.token),
        None,
    )
    .await;
    let (filtered_status, filtered_body) = response_json(filtered).await;
    assert_eq!(filtered_status, StatusCode::OK);
    assert_eq!(filtered_body["history"]["events"].as_array().unwrap().len(), 1);

    let conn = db::connect(&fixture.db_path).unwrap();
    let after_events: i64 = conn
        .query_row("SELECT COUNT(*) FROM authorization_admin_events", [], |row| row.get(0))
        .unwrap();
    assert_eq!(after_events, before_events, "history read mutated provenance");
}

#[tokio::test]
async fn authorization_administration_history_rest_refuses_stale_schema_without_repair() {
    let fixture = fixture("authorization-admin-history-stale");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let conn = db::connect(&fixture.db_path).unwrap();
    identity::set_web_participant_role(&conn, &fixture.participant_id, "admin").unwrap();
    conn.execute("DROP TABLE authorization_admin_events", []).unwrap();
    drop(conn);
    let session = web_auth::issue_web_session(&fixture.participant_id);

    let response = request(
        &router,
        Method::GET,
        "/api/authorization-administration/history",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);

    let conn = db::connect(&fixture.db_path).unwrap();
    let table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'authorization_admin_events'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(table_count, 0, "history read repaired stale schema");
}
'''
if "authorization_administration_history_rest_requires_dedicated_authority_and_is_read_only" in tests:
    raise RuntimeError("history REST tests already exist")
test_path.write_text(tests.rstrip() + append + "\n")
