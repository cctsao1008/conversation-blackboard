from pathlib import Path

ACCESS = Path('src/access_api.rs')
ADMIN = Path('src/authorization_admin.rs')
TESTS = Path('src/http_contract_tests.rs')

admin = ADMIN.read_text()
start = admin.index('pub fn deactivate_durable_grant(\n')
end = admin.index('pub fn create_delegated_grant(\n', start)
replacement = r'''fn deactivate_durable_grant_in_tx(
    tx: &Transaction<'_>,
    actor: &NormalizedActor,
    grant_id: i64,
) -> AdministrationResult<Option<GrantDeactivateOutcome>> {
    if grant_id <= 0 {
        return Err("grant_id must be positive".into());
    }
    let scope: Option<(String, String, String, String, Option<String>)> = tx
        .query_row(
            "SELECT principal_provider, principal_subject, participant_id, capability, resource
             FROM principal_grants WHERE id = ?1",
            [grant_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                ))
            },
        )
        .optional()?;
    let Some((provider, subject, participant_id, capability, resource)) = scope else {
        return Ok(None);
    };

    let active_ids = {
        let mut stmt = tx.prepare(
            "SELECT id FROM principal_grants
             WHERE principal_provider = ?1
               AND principal_subject = ?2
               AND participant_id = ?3
               AND capability = ?4
               AND resource IS ?5
               AND status != 'inactive'
             ORDER BY id",
        )?;
        let rows = stmt
            .query_map(
                params![
                    &provider,
                    &subject,
                    &participant_id,
                    &capability,
                    resource.as_deref(),
                ],
                |row| row.get::<_, i64>(0),
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };

    let updated = tx.execute(
        "UPDATE principal_grants
         SET status = 'inactive', updated_at = unixepoch()
         WHERE principal_provider = ?1
           AND principal_subject = ?2
           AND participant_id = ?3
           AND capability = ?4
           AND resource IS ?5
           AND status != 'inactive'",
        params![
            &provider,
            &subject,
            &participant_id,
            &capability,
            resource.as_deref(),
        ],
    )?;

    for id in &active_ids {
        record_event(
            tx,
            actor,
            &AdministrationEvent {
                grant_store: "durable",
                grant_id: *id,
                operation: "deactivate",
                scope: GrantScope {
                    principal_provider: &provider,
                    principal_subject: &subject,
                    participant_id: &participant_id,
                    capability: &capability,
                    resource: resource.as_deref(),
                    intent_id: None,
                    expires_at: None,
                    one_shot: false,
                },
                before_status: Some("active"),
                after_status: "inactive",
            },
        )?;
    }
    debug_assert_eq!(updated, active_ids.len());
    Ok(Some(GrantDeactivateOutcome {
        rows_changed: updated,
    }))
}

pub fn deactivate_durable_grant(
    conn: &Connection,
    actor: &AuthorizationAdministrationActor<'_>,
    grant_id: i64,
) -> AdministrationResult<Option<GrantDeactivateOutcome>> {
    require_schema_current(conn)?;
    let actor = normalize_actor(actor)?;
    let tx = conn.unchecked_transaction()?;
    let outcome = deactivate_durable_grant_in_tx(&tx, &actor, grant_id)?;
    tx.commit()?;
    Ok(outcome)
}

pub fn deactivate_durable_grant_authorized(
    conn: &Connection,
    surface: &str,
    caller_principal: &Principal,
    caller_participant_id: &str,
    grant_id: i64,
) -> AdministrationResult<Option<GrantDeactivateOutcome>> {
    require_schema_current(conn)?;
    let actor_spec = AuthorizationAdministrationActor {
        surface,
        principal: Some(caller_principal),
        participant_id: Some(caller_participant_id),
    };
    let actor = normalize_actor(&actor_spec)?;
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
    let outcome = deactivate_durable_grant_in_tx(&tx, &actor, grant_id)?;
    tx.commit()?;
    Ok(outcome)
}

'''
admin = admin[:start] + replacement + admin[end:]

marker = '''    #[test]\n    fn explicit_remote_administration_grant_allows_external_principal() {'''
assert marker in admin
unit_test = r'''    #[test]
    fn authorized_durable_deactivate_checks_authority_and_mutates_in_one_boundary() {
        let (_dir, conn) = setup();
        identity::provision_web_participant_identity(
            &conn,
            "admin-main",
            "admin",
            Some("Administrator"),
        )
        .unwrap()
        .unwrap();
        identity::set_web_participant_role(&conn, "admin-main", "admin").unwrap();
        let local = AuthorizationAdministrationActor::local_cli();
        let request = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "deactivate-agent",
            participant_id: "maker-main",
            capability: authorization::READ_MESSAGES,
            resource: None,
        };
        let created = create_durable_grant(&conn, &local, &request).unwrap();
        let before = event_count(&conn);

        let ordinary = Principal {
            provider: "human-web".to_owned(),
            subject: "maker-main".to_owned(),
        };
        assert!(deactivate_durable_grant_authorized(
            &conn,
            "rest-test",
            &ordinary,
            "maker-main",
            created.id,
        )
        .is_err());
        let status: String = conn
            .query_row(
                "SELECT status FROM principal_grants WHERE id = ?1",
                [created.id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(status, "active");
        assert_eq!(event_count(&conn), before);

        let admin = Principal {
            provider: "human-web".to_owned(),
            subject: "admin-main".to_owned(),
        };
        let deactivated = deactivate_durable_grant_authorized(
            &conn,
            "rest-test",
            &admin,
            "admin-main",
            created.id,
        )
        .unwrap()
        .unwrap();
        assert_eq!(deactivated.rows_changed, 1);
        assert_eq!(event_count(&conn), before + 1);
        let event: (String, String, String, String) = conn
            .query_row(
                "SELECT actor_surface, actor_provider, actor_subject, actor_participant_id
                 FROM authorization_admin_events
                 WHERE grant_store = 'durable' AND grant_id = ?1 AND operation = 'deactivate'",
                [created.id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .unwrap();
        assert_eq!(event.0, "rest-test");
        assert_eq!(event.1, "human-web");
        assert_eq!(event.2, "admin-main");
        assert_eq!(event.3, "admin-main");

        let repeated = deactivate_durable_grant_authorized(
            &conn,
            "rest-test",
            &admin,
            "admin-main",
            created.id,
        )
        .unwrap()
        .unwrap();
        assert_eq!(repeated.rows_changed, 0);
        assert_eq!(event_count(&conn), before + 1);
    }

'''
admin = admin.replace(marker, unit_test + marker, 1)
ADMIN.write_text(admin)

access = ACCESS.read_text()
old = '    routing::{get, post},\n'
new = '    routing::{delete, get, post},\n'
assert old in access
access = access.replace(old, new, 1)
old = '''        .route(\n            "/api/authorization-grants/durable",\n            post(create_durable_authorization_grant),\n        )\n        .with_state(state)'''
new = '''        .route(\n            "/api/authorization-grants/durable",\n            post(create_durable_authorization_grant),\n        )\n        .route(\n            "/api/authorization-grants/durable/{grant_id}",\n            delete(deactivate_durable_authorization_grant),\n        )\n        .with_state(state)'''
assert old in access
access = access.replace(old, new, 1)

marker = 'fn map_administration_error(error: Box<dyn std::error::Error + Send + Sync>) -> AccessApiError {\n'
assert marker in access
handler = r'''async fn deactivate_durable_authorization_grant(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(grant_id): Path<i64>,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "DELETE", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    if principal.provider == "participant-hmac" {
        return Err(AccessApiError::new(
            StatusCode::FORBIDDEN,
            "unsupported_authentication",
        ));
    }

    let caller_participant_id = resolved.instance.clone();
    let outcome = with_db_access(&state, move |conn| {
        authorization_admin::deactivate_durable_grant_authorized(
            conn,
            "rest-authorization-admin",
            &principal,
            &caller_participant_id,
            grant_id,
        )
        .map_err(map_administration_error)
    })
    .await?
    .ok_or_else(|| AccessApiError::new(StatusCode::NOT_FOUND, "grant_not_found"))?;

    Ok(json_response(
        StatusCode::OK,
        json!({
            "grant": {
                "store": "durable",
                "id": grant_id,
                "state": "inactive",
                "rows_changed": outcome.rows_changed,
            }
        }),
    ))
}

'''
access = access.replace(marker, handler + marker, 1)
ACCESS.write_text(access)

tests = TESTS.read_text()
old = '''use crate::{\n    authorization, db, execution,'''
new = '''use crate::{\n    authorization, authorization_admin, db, execution,'''
assert old in tests
tests = tests.replace(old, new, 1)
marker = '''#[tokio::test]\nasync fn authorization_policy_http_is_privileged_canonical_and_observationally_read_only() {'''
assert marker in tests
http_test = r'''#[tokio::test]
async fn authorization_admin_rest_deactivate_is_privileged_idempotent_and_observationally_strict() {
    let fixture = fixture("authorization-admin-deactivate");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let conn = db::connect(&fixture.db_path).unwrap();
    let local = authorization_admin::AuthorizationAdministrationActor::local_cli();
    let request_seed = authorization_admin::DurableGrantCreateRequest {
        principal_provider: "oidc:https://issuer.example",
        principal_subject: "deactivate-worker",
        participant_id: &fixture.participant_id,
        capability: authorization::READ_MESSAGES,
        resource: None,
    };
    let created = authorization_admin::create_durable_grant(&conn, &local, &request_seed).unwrap();
    drop(conn);
    let uri = format!("/api/authorization-grants/durable/{}", created.id);

    let unauthenticated = request(&router, Method::DELETE, &uri, None, None).await;
    assert_eq!(unauthenticated.status(), StatusCode::UNAUTHORIZED);

    let canonical = web_auth::canonical_http_request_bytes(&fixture.participant_id, "DELETE", &uri);
    let proof = participant_auth::compute_message_proof(&fixture.signing_private, &canonical).unwrap();
    let hmac = router
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::DELETE)
                .uri(&uri)
                .header(request_auth::PARTICIPANT_ID_HEADER, &fixture.participant_id)
                .header(
                    request_auth::AUTH_SCHEME_HEADER,
                    participant_auth::AUTH_SCHEME,
                )
                .header(request_auth::AUTH_PROOF_HEADER, proof)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(hmac.status(), StatusCode::FORBIDDEN);

    let session = web_auth::issue_web_session(&fixture.participant_id);
    let ordinary = request(&router, Method::DELETE, &uri, Some(&session.token), None).await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    let conn = db::connect(&fixture.db_path).unwrap();
    identity::set_web_participant_role(&conn, &fixture.participant_id, "admin").unwrap();
    drop(conn);

    let deactivated = request(&router, Method::DELETE, &uri, Some(&session.token), None).await;
    let (status, body) = response_json(deactivated).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["grant"]["store"], "durable");
    assert_eq!(body["grant"]["id"], created.id);
    assert_eq!(body["grant"]["state"], "inactive");
    assert_eq!(body["grant"]["rows_changed"], 1);

    let conn = db::connect(&fixture.db_path).unwrap();
    let grant_status: String = conn
        .query_row(
            "SELECT status FROM principal_grants WHERE id = ?1",
            [created.id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(grant_status, "inactive");
    let event: (String, String, String, String) = conn
        .query_row(
            "SELECT actor_surface, actor_provider, actor_subject, actor_participant_id
             FROM authorization_admin_events
             WHERE grant_store = 'durable' AND grant_id = ?1 AND operation = 'deactivate'",
            [created.id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .unwrap();
    assert_eq!(event.0, "rest-authorization-admin");
    assert_eq!(event.1, "human-web");
    assert_eq!(event.2, fixture.participant_id);
    assert_eq!(event.3, fixture.participant_id);
    let before_repeat: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM authorization_admin_events
             WHERE grant_store = 'durable' AND grant_id = ?1 AND operation = 'deactivate'",
            [created.id],
            |row| row.get(0),
        )
        .unwrap();
    drop(conn);

    let repeated = request(&router, Method::DELETE, &uri, Some(&session.token), None).await;
    let (repeat_status, repeat_body) = response_json(repeated).await;
    assert_eq!(repeat_status, StatusCode::OK);
    assert_eq!(repeat_body["grant"]["rows_changed"], 0);
    let conn = db::connect(&fixture.db_path).unwrap();
    let after_repeat: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM authorization_admin_events
             WHERE grant_store = 'durable' AND grant_id = ?1 AND operation = 'deactivate'",
            [created.id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(after_repeat, before_repeat);
    drop(conn);

    let missing = request(
        &router,
        Method::DELETE,
        "/api/authorization-grants/durable/9223372036854770000",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(missing.status(), StatusCode::NOT_FOUND);
    let invalid = request(
        &router,
        Method::DELETE,
        "/api/authorization-grants/durable/0",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(invalid.status(), StatusCode::BAD_REQUEST);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE authorization_admin_events", []).unwrap();
    drop(conn);
    let stale = request(&router, Method::DELETE, &uri, Some(&session.token), None).await;
    assert_eq!(stale.status(), StatusCode::SERVICE_UNAVAILABLE);
    let conn = db::connect(&fixture.db_path).unwrap();
    let admin_table: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master
             WHERE type = 'table' AND name = 'authorization_admin_events'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(admin_table, 0, "REST deactivate repaired stale schema");
}

'''
tests = tests.replace(marker, http_test + marker, 1)
TESTS.write_text(tests)
