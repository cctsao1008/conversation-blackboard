from pathlib import Path

ACCESS = Path('src/access_api.rs')
ADMIN = Path('src/authorization_admin.rs')
TESTS = Path('src/http_contract_tests.rs')

access = ACCESS.read_text()

access = access.replace('    routing::get,\n', '    routing::{get, post},\n', 1)
old = '''use crate::{\n    authorization, execution, http::AppState, identity, model::Identity, request_auth, web_auth,\n};'''
new = '''use crate::{\n    authorization, authorization_admin, execution, http::AppState, identity, model::Identity,\n    request_auth, web_auth,\n};'''
assert old in access
access = access.replace(old, new, 1)

old = '''        .route(\n            "/api/authorization-decision/explain",\n            get(authorization_decision_explain),\n        )\n        .with_state(state)'''
new = '''        .route(\n            "/api/authorization-decision/explain",\n            get(authorization_decision_explain),\n        )\n        .route(\n            "/api/authorization-grants/durable",\n            post(create_durable_authorization_grant),\n        )\n        .with_state(state)'''
assert old in access
access = access.replace(old, new, 1)

marker = 'async fn access_context(\n'
assert marker in access
insert = r'''#[derive(Debug, Deserialize)]
struct DurableGrantCreateBody {
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
}

async fn create_durable_authorization_grant(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<DurableGrantCreateBody>,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "POST", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    if principal.provider == "participant-hmac" {
        return Err(AccessApiError::new(
            StatusCode::FORBIDDEN,
            "unsupported_authentication",
        ));
    }

    let caller_participant_id = resolved.instance.clone();
    let state_name;
    let outcome = with_db_access(&state, move |conn| {
        let request = authorization_admin::DurableGrantCreateRequest {
            principal_provider: &body.principal_provider,
            principal_subject: &body.principal_subject,
            participant_id: &body.participant_id,
            capability: &body.capability,
            resource: body.resource.as_deref(),
        };
        authorization_admin::create_durable_grant_authorized(
            conn,
            "rest-authorization-admin",
            &principal,
            &caller_participant_id,
            &request,
        )
        .map_err(map_administration_error)
    })
    .await?;

    state_name = match outcome.state {
        authorization_admin::DurableGrantCreateState::Created => "created",
        authorization_admin::DurableGrantCreateState::Existing => "existing",
        authorization_admin::DurableGrantCreateState::Reactivated => "reactivated",
    };
    let status = if outcome.state == authorization_admin::DurableGrantCreateState::Created {
        StatusCode::CREATED
    } else {
        StatusCode::OK
    };
    Ok(json_response(
        status,
        json!({
            "grant": {
                "store": "durable",
                "id": outcome.id,
                "state": state_name,
            }
        }),
    ))
}

fn map_administration_error(
    error: Box<dyn std::error::Error + Send + Sync>,
) -> AccessApiError {
    let message = error.to_string();
    if message == "authorization denied" {
        return AccessApiError::new(StatusCode::FORBIDDEN, "forbidden");
    }
    if message.contains("schema requires migration") {
        return AccessApiError::new(StatusCode::SERVICE_UNAVAILABLE, "schema_migration_required");
    }
    if error.downcast_ref::<rusqlite::Error>().is_some() {
        return AccessApiError::new(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable");
    }
    AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_grant_request")
}

'''
access = access.replace(marker, insert + marker, 1)

marker = 'async fn with_db_read_only<T, F>(state: &AppState, operation: F) -> Result<T, AccessApiError>\n'
assert marker in access
helper = r'''async fn with_db_access<T, F>(state: &AppState, operation: F) -> Result<T, AccessApiError>
where
    T: Send + 'static,
    F: FnOnce(&rusqlite::Connection) -> Result<T, AccessApiError> + Send + 'static,
{
    let path = state.db_path.clone();
    tokio::task::spawn_blocking(move || {
        let conn = crate::db::connect(&path)
            .map_err(|_| AccessApiError::new(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable"))?;
        operation(&conn)
    })
    .await
    .map_err(|_| AccessApiError::new(StatusCode::INTERNAL_SERVER_ERROR, "internal_error"))?
}

'''
access = access.replace(marker, helper + marker, 1)
ACCESS.write_text(access)

admin = ADMIN.read_text()
old = '''#[cfg(test)]\npub fn create_durable_grant_authorized('''
assert old in admin
admin = admin.replace(old, 'pub fn create_durable_grant_authorized(', 1)
ADMIN.write_text(admin)

tests = TESTS.read_text()
marker = '''#[tokio::test]\nasync fn authorization_policy_http_is_privileged_canonical_and_observationally_read_only() {'''
assert marker in tests
new_tests = r'''#[tokio::test]
async fn authorization_admin_rest_create_is_privileged_audited_and_hmac_rejected() {
    let fixture = fixture("authorization-admin-http");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let uri = "/api/authorization-grants/durable";
    let body = json!({
        "principal_provider": "oidc:https://issuer.example",
        "principal_subject": "worker-agent",
        "participant_id": fixture.participant_id,
        "capability": "read_messages",
        "resource": null,
    });

    let unauthenticated = request(&router, Method::POST, uri, None, Some(body.clone())).await;
    assert_eq!(unauthenticated.status(), StatusCode::UNAUTHORIZED);

    let canonical = web_auth::canonical_http_request_bytes(&fixture.participant_id, "POST", uri);
    let proof = participant_auth::compute_message_proof(&fixture.signing_private, &canonical).unwrap();
    let hmac = router
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri(uri)
                .header(header::CONTENT_TYPE, "application/json")
                .header(request_auth::PARTICIPANT_ID_HEADER, &fixture.participant_id)
                .header(
                    request_auth::AUTH_SCHEME_HEADER,
                    participant_auth::AUTH_SCHEME,
                )
                .header(request_auth::AUTH_PROOF_HEADER, proof)
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(hmac.status(), StatusCode::FORBIDDEN);

    let session = web_auth::issue_web_session(&fixture.participant_id);
    let ordinary = request(
        &router,
        Method::POST,
        uri,
        Some(&session.token),
        Some(body.clone()),
    )
    .await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    let conn = db::connect(&fixture.db_path).unwrap();
    identity::set_web_participant_role(&conn, &fixture.participant_id, "admin").unwrap();
    drop(conn);

    let created = request(
        &router,
        Method::POST,
        uri,
        Some(&session.token),
        Some(body.clone()),
    )
    .await;
    let (created_status, created_body) = response_json(created).await;
    assert_eq!(created_status, StatusCode::CREATED);
    assert_eq!(created_body["grant"]["store"], "durable");
    assert_eq!(created_body["grant"]["state"], "created");
    let grant_id = created_body["grant"]["id"].as_i64().unwrap();

    let conn = db::connect(&fixture.db_path).unwrap();
    let event: (String, Option<String>, Option<String>, Option<String>) = conn
        .query_row(
            "SELECT actor_surface, actor_provider, actor_subject, actor_participant_id
             FROM authorization_admin_events
             WHERE grant_store = 'durable' AND grant_id = ?1",
            [grant_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .unwrap();
    assert_eq!(event.0, "rest-authorization-admin");
    assert_eq!(event.1.as_deref(), Some("human-web"));
    assert_eq!(event.2.as_deref(), Some(fixture.participant_id.as_str()));
    assert_eq!(event.3.as_deref(), Some(fixture.participant_id.as_str()));
    let event_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM authorization_admin_events WHERE grant_id = ?1",
            [grant_id],
            |row| row.get(0),
        )
        .unwrap();
    drop(conn);

    let existing = request(
        &router,
        Method::POST,
        uri,
        Some(&session.token),
        Some(body),
    )
    .await;
    let (existing_status, existing_body) = response_json(existing).await;
    assert_eq!(existing_status, StatusCode::OK);
    assert_eq!(existing_body["grant"]["state"], "existing");
    assert_eq!(existing_body["grant"]["id"], grant_id);
    let conn = db::connect(&fixture.db_path).unwrap();
    let after_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM authorization_admin_events WHERE grant_id = ?1",
            [grant_id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(after_count, event_count, "idempotent create emitted a false event");
}

#[tokio::test]
async fn authorization_admin_rest_create_requires_explicit_bearer_authority() {
    let fixture = fixture("authorization-admin-bearer");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let uri = "/api/authorization-grants/durable";
    let conn = db::connect(&fixture.db_path).unwrap();
    let (bearer_identity, bearer_token) =
        identity::register_identity(&conn, "remote-admin", Some("Remote admin")).unwrap();
    identity::provision_web_participant_identity(
        &conn,
        &bearer_identity.instance,
        "remote-admin",
        Some("Remote admin participant"),
    )
    .unwrap()
    .unwrap();
    drop(conn);

    let body = json!({
        "principal_provider": "oidc:https://issuer.example",
        "principal_subject": "remote-worker",
        "participant_id": fixture.participant_id,
        "capability": "read_messages",
        "resource": null,
    });
    let bearer_request = |body: Value| {
        Request::builder()
            .method(Method::POST)
            .uri(uri)
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::AUTHORIZATION, format!("Bearer {bearer_token}"))
            .body(Body::from(body.to_string()))
            .unwrap()
    };

    let denied = router
        .clone()
        .oneshot(bearer_request(body.clone()))
        .await
        .unwrap();
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('bearer', ?1, ?1, ?2, ?3)",
        rusqlite::params![
            &bearer_identity.instance,
            authorization::MANAGE_AUTHORIZATION_POLICY,
            authorization::AUTHORIZATION_POLICY_ADMIN_RESOURCE,
        ],
    )
    .unwrap();
    drop(conn);

    let allowed = router
        .clone()
        .oneshot(bearer_request(body))
        .await
        .unwrap();
    let (status, response) = response_json(allowed).await;
    assert_eq!(status, StatusCode::CREATED);
    let grant_id = response["grant"]["id"].as_i64().unwrap();
    let conn = db::connect(&fixture.db_path).unwrap();
    let actor: (String, String) = conn
        .query_row(
            "SELECT actor_provider, actor_subject FROM authorization_admin_events
             WHERE grant_store = 'durable' AND grant_id = ?1",
            [grant_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap();
    assert_eq!(actor.0, "bearer");
    assert_eq!(actor.1, bearer_identity.instance);
}

#[tokio::test]
async fn authorization_admin_rest_create_rejects_stale_schema_without_repair() {
    let fixture = fixture("authorization-admin-stale");
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
    let body = json!({
        "principal_provider": "oidc:https://issuer.example",
        "principal_subject": "stale-worker",
        "participant_id": fixture.participant_id,
        "capability": "read_messages",
        "resource": null,
    });
    let response = request(
        &router,
        Method::POST,
        "/api/authorization-grants/durable",
        Some(&session.token),
        Some(body),
    )
    .await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let conn = db::connect(&fixture.db_path).unwrap();
    let table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master
             WHERE type = 'table' AND name = 'authorization_admin_events'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(table_count, 0, "REST mutation repaired stale schema");
    let target_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM principal_grants WHERE principal_subject = 'stale-worker'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(target_count, 0);
}

'''
tests = tests.replace(marker, new_tests + marker, 1)
TESTS.write_text(tests)
