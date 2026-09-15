from pathlib import Path

ACCESS = Path('src/access_api.rs')
ADMIN = Path('src/authorization_admin.rs')
TESTS = Path('src/http_contract_tests.rs')

admin = ADMIN.read_text()
marker = '''struct NormalizedDurableGrantCreate {\n    principal_provider: String,\n    principal_subject: String,\n    participant_id: String,\n    capability: String,\n    resource: Option<String>,\n}\n'''
assert marker in admin
insert = marker + r'''
#[derive(Debug)]
struct NormalizedDelegatedGrantCreate {
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
    intent_id: Option<String>,
    expires_at: Option<i64>,
    one_shot: bool,
}
'''
admin = admin.replace(marker, insert, 1)

start = admin.index('pub fn create_delegated_grant(\n')
end = admin.index('pub fn deactivate_delegated_grant(\n', start)
replacement = r'''fn normalize_delegated_grant_create_request(
    request: &DelegatedGrantCreateRequest<'_>,
) -> AdministrationResult<NormalizedDelegatedGrantCreate> {
    let principal_provider = normalize(
        request.principal_provider,
        MAX_PROVIDER_BYTES,
        "principal_provider",
    )?;
    let principal_subject = normalize(
        request.principal_subject,
        MAX_SUBJECT_BYTES,
        "principal_subject",
    )?;
    let participant_id = identity::validate_participant_id(request.participant_id)
        .ok_or("invalid participant_id")?;
    let capability = normalize(request.capability, MAX_CAPABILITY_BYTES, "capability")?;
    let resource = normalize_optional(request.resource, MAX_RESOURCE_BYTES, "resource")?;
    let intent_id = request
        .intent_id
        .map(|value| execution::normalize_intent_id(value).map_err(|_| "invalid intent_id"))
        .transpose()?;
    Ok(NormalizedDelegatedGrantCreate {
        principal_provider,
        principal_subject,
        participant_id,
        capability,
        resource,
        intent_id,
        expires_at: request.expires_at,
        one_shot: request.one_shot,
    })
}

fn create_delegated_grant_in_tx(
    tx: &Transaction<'_>,
    actor: &NormalizedActor,
    request: &NormalizedDelegatedGrantCreate,
) -> AdministrationResult<DelegatedGrantCreateOutcome> {
    require_active_participant(tx, &request.participant_id)?;
    if let Some(expires_at) = request.expires_at {
        let now: i64 = tx.query_row("SELECT unixepoch()", [], |row| row.get(0))?;
        if expires_at <= now {
            return Err("expires_at must be a future Unix timestamp".into());
        }
    }

    tx.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource, intent_id, expires_at, one_shot)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            &request.principal_provider,
            &request.principal_subject,
            &request.participant_id,
            &request.capability,
            request.resource.as_deref(),
            request.intent_id.as_deref(),
            request.expires_at,
            i64::from(request.one_shot),
        ],
    )?;
    let id = tx.last_insert_rowid();
    record_event(
        tx,
        actor,
        &AdministrationEvent {
            grant_store: "delegated",
            grant_id: id,
            operation: "create",
            scope: GrantScope {
                principal_provider: &request.principal_provider,
                principal_subject: &request.principal_subject,
                participant_id: &request.participant_id,
                capability: &request.capability,
                resource: request.resource.as_deref(),
                intent_id: request.intent_id.as_deref(),
                expires_at: request.expires_at,
                one_shot: request.one_shot,
            },
            before_status: None,
            after_status: "active",
        },
    )?;
    Ok(DelegatedGrantCreateOutcome { id })
}

pub fn create_delegated_grant(
    conn: &Connection,
    actor: &AuthorizationAdministrationActor<'_>,
    request: &DelegatedGrantCreateRequest<'_>,
) -> AdministrationResult<DelegatedGrantCreateOutcome> {
    require_schema_current(conn)?;
    let actor = normalize_actor(actor)?;
    let request = normalize_delegated_grant_create_request(request)?;
    let tx = conn.unchecked_transaction()?;
    let outcome = create_delegated_grant_in_tx(&tx, &actor, &request)?;
    tx.commit()?;
    Ok(outcome)
}

pub fn create_delegated_grant_authorized(
    conn: &Connection,
    surface: &str,
    caller_principal: &Principal,
    caller_participant_id: &str,
    request: &DelegatedGrantCreateRequest<'_>,
) -> AdministrationResult<DelegatedGrantCreateOutcome> {
    require_schema_current(conn)?;
    let actor_spec = AuthorizationAdministrationActor {
        surface,
        principal: Some(caller_principal),
        participant_id: Some(caller_participant_id),
    };
    let actor = normalize_actor(&actor_spec)?;
    let request = normalize_delegated_grant_create_request(request)?;
    let tx = conn.unchecked_transaction()?;
    let decision = authorization::explain_authorization(
        &tx,
        caller_principal,
        caller_participant_id,
        authorization::MANAGE_AUTHORIZATION_POLICY,
        Some(authorization::AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        None,
    )?;
    if !decision.allowed {
        return Err("authorization denied".into());
    }
    let outcome = create_delegated_grant_in_tx(&tx, &actor, &request)?;
    tx.commit()?;
    Ok(outcome)
}

'''
admin = admin[:start] + replacement + admin[end:]
ADMIN.write_text(admin)

access = ACCESS.read_text()
old = '''        .route(\n            "/api/authorization-grants/durable/{grant_id}",\n            delete(deactivate_durable_authorization_grant),\n        )\n        .with_state(state)'''
new = '''        .route(\n            "/api/authorization-grants/durable/{grant_id}",\n            delete(deactivate_durable_authorization_grant),\n        )\n        .route(\n            "/api/authorization-grants/delegated",\n            post(create_delegated_authorization_grant),\n        )\n        .with_state(state)'''
assert old in access
access = access.replace(old, new, 1)

marker = '''async fn deactivate_durable_authorization_grant(\n'''
assert marker in access
handler = r'''#[derive(Debug, Deserialize)]
struct DelegatedGrantCreateBody {
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
    intent_id: Option<String>,
    expires_at: Option<i64>,
    one_shot: bool,
}

async fn create_delegated_authorization_grant(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<DelegatedGrantCreateBody>,
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
    let outcome = with_db_access(&state, move |conn| {
        let request = authorization_admin::DelegatedGrantCreateRequest {
            principal_provider: &body.principal_provider,
            principal_subject: &body.principal_subject,
            participant_id: &body.participant_id,
            capability: &body.capability,
            resource: body.resource.as_deref(),
            intent_id: body.intent_id.as_deref(),
            expires_at: body.expires_at,
            one_shot: body.one_shot,
        };
        authorization_admin::create_delegated_grant_authorized(
            conn,
            "rest-authorization-admin",
            &principal,
            &caller_participant_id,
            &request,
        )
        .map_err(map_administration_error)
    })
    .await?;

    Ok(json_response(
        StatusCode::CREATED,
        json!({
            "grant": {
                "store": "delegated",
                "id": outcome.id,
                "state": "created",
            }
        }),
    ))
}

'''
access = access.replace(marker, handler + marker, 1)
ACCESS.write_text(access)

tests = TESTS.read_text()
marker = '''#[tokio::test]\nasync fn authorization_policy_http_is_privileged_canonical_and_observationally_read_only() {'''
assert marker in tests
http_test = r'''#[tokio::test]
async fn authorization_admin_rest_delegated_create_preserves_scope_validation_and_provenance() {
    let fixture = fixture("authorization-admin-delegated-create");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let uri = "/api/authorization-grants/delegated";
    let now: i64 = db::connect(&fixture.db_path)
        .unwrap()
        .query_row("SELECT unixepoch()", [], |row| row.get(0))
        .unwrap();
    let body = json!({
        "principal_provider": "oidc:https://issuer.example",
        "principal_subject": "delegated-worker",
        "participant_id": fixture.participant_id,
        "capability": "post_message",
        "resource": "control-systems",
        "intent_id": "intent-rest-delegated-108",
        "expires_at": now + 3600,
        "one_shot": true,
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
        Some(body),
    )
    .await;
    let (status, response) = response_json(created).await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(response["grant"]["store"], "delegated");
    assert_eq!(response["grant"]["state"], "created");
    let grant_id = response["grant"]["id"].as_i64().unwrap();

    let conn = db::connect(&fixture.db_path).unwrap();
    let row: (String, String, String, String, Option<String>, Option<String>, Option<i64>, i64, String) = conn
        .query_row(
            "SELECT principal_provider, principal_subject, participant_id, capability,
                    resource, intent_id, expires_at, one_shot, status
             FROM delegated_grants WHERE id = ?1",
            [grant_id],
            |row| Ok((
                row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?,
                row.get(5)?, row.get(6)?, row.get(7)?, row.get(8)?,
            )),
        )
        .unwrap();
    assert_eq!(row.0, "oidc:https://issuer.example");
    assert_eq!(row.1, "delegated-worker");
    assert_eq!(row.2, fixture.participant_id);
    assert_eq!(row.3, "post_message");
    assert_eq!(row.4.as_deref(), Some("control-systems"));
    assert_eq!(row.5.as_deref(), Some("intent-rest-delegated-108"));
    assert_eq!(row.6, Some(now + 3600));
    assert_eq!(row.7, 1);
    assert_eq!(row.8, "active");
    let event: (String, String, String, String, Option<String>, i64) = conn
        .query_row(
            "SELECT actor_surface, actor_provider, actor_subject, intent_id, resource, one_shot
             FROM authorization_admin_events
             WHERE grant_store = 'delegated' AND grant_id = ?1 AND operation = 'create'",
            [grant_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?)),
        )
        .unwrap();
    assert_eq!(event.0, "rest-authorization-admin");
    assert_eq!(event.1, "human-web");
    assert_eq!(event.2, fixture.participant_id);
    assert_eq!(event.3, "intent-rest-delegated-108");
    assert_eq!(event.4.as_deref(), Some("control-systems"));
    assert_eq!(event.5, 1);
    let before_invalid: i64 = conn
        .query_row("SELECT COUNT(*) FROM authorization_admin_events", [], |row| row.get(0))
        .unwrap();
    drop(conn);

    let invalid = json!({
        "principal_provider": "oidc:https://issuer.example",
        "principal_subject": "expired-worker",
        "participant_id": fixture.participant_id,
        "capability": "post_message",
        "resource": null,
        "intent_id": "intent-expired-108",
        "expires_at": now - 1,
        "one_shot": false,
    });
    let invalid_response = request(
        &router,
        Method::POST,
        uri,
        Some(&session.token),
        Some(invalid),
    )
    .await;
    assert_eq!(invalid_response.status(), StatusCode::BAD_REQUEST);
    let conn = db::connect(&fixture.db_path).unwrap();
    let expired_rows: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM delegated_grants WHERE principal_subject = 'expired-worker'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(expired_rows, 0);
    let after_invalid: i64 = conn
        .query_row("SELECT COUNT(*) FROM authorization_admin_events", [], |row| row.get(0))
        .unwrap();
    assert_eq!(after_invalid, before_invalid);
    conn.execute("DROP TABLE authorization_admin_events", []).unwrap();
    drop(conn);

    let stale = json!({
        "principal_provider": "oidc:https://issuer.example",
        "principal_subject": "stale-delegated-worker",
        "participant_id": fixture.participant_id,
        "capability": "read_messages",
        "resource": null,
        "intent_id": null,
        "expires_at": null,
        "one_shot": false,
    });
    let stale_response = request(
        &router,
        Method::POST,
        uri,
        Some(&session.token),
        Some(stale),
    )
    .await;
    assert_eq!(stale_response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let conn = db::connect(&fixture.db_path).unwrap();
    let admin_table: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master
             WHERE type = 'table' AND name = 'authorization_admin_events'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(admin_table, 0, "REST delegated create repaired stale schema");
    let stale_rows: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM delegated_grants WHERE principal_subject = 'stale-delegated-worker'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(stale_rows, 0);
}

'''
tests = tests.replace(marker, http_test + marker, 1)
TESTS.write_text(tests)
