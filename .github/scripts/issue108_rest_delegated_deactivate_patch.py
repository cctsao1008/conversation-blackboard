from pathlib import Path

ACCESS = Path('src/access_api.rs')
ADMIN = Path('src/authorization_admin.rs')
TESTS = Path('src/http_contract_tests.rs')

admin = ADMIN.read_text()
start = admin.index('pub fn deactivate_delegated_grant(\n')
end = admin.index('fn normalize_actor(\n', start)
replacement = r'''fn deactivate_delegated_grant_in_tx(
    tx: &Transaction<'_>,
    actor: &NormalizedActor,
    grant_id: i64,
) -> AdministrationResult<Option<GrantDeactivateOutcome>> {
    if grant_id <= 0 {
        return Err("grant_id must be positive".into());
    }
    let scope: Option<DelegatedGrantState> = tx
        .query_row(
            "SELECT principal_provider, principal_subject, participant_id, capability,
                    resource, intent_id, expires_at, one_shot, status
             FROM delegated_grants WHERE id = ?1",
            [grant_id],
            |row| {
                Ok(DelegatedGrantState {
                    principal_provider: row.get(0)?,
                    principal_subject: row.get(1)?,
                    participant_id: row.get(2)?,
                    capability: row.get(3)?,
                    resource: row.get(4)?,
                    intent_id: row.get(5)?,
                    expires_at: row.get(6)?,
                    one_shot: row.get::<_, i64>(7)? != 0,
                    status: row.get(8)?,
                })
            },
        )
        .optional()?;
    let Some(scope) = scope else {
        return Ok(None);
    };

    let updated = tx.execute(
        "UPDATE delegated_grants
         SET status = 'inactive', updated_at = unixepoch()
         WHERE id = ?1 AND status != 'inactive'",
        [grant_id],
    )?;
    if updated == 1 {
        record_event(
            tx,
            actor,
            &AdministrationEvent {
                grant_store: "delegated",
                grant_id,
                operation: "deactivate",
                scope: GrantScope {
                    principal_provider: &scope.principal_provider,
                    principal_subject: &scope.principal_subject,
                    participant_id: &scope.participant_id,
                    capability: &scope.capability,
                    resource: scope.resource.as_deref(),
                    intent_id: scope.intent_id.as_deref(),
                    expires_at: scope.expires_at,
                    one_shot: scope.one_shot,
                },
                before_status: Some(&scope.status),
                after_status: "inactive",
            },
        )?;
    }
    Ok(Some(GrantDeactivateOutcome {
        rows_changed: updated,
    }))
}

pub fn deactivate_delegated_grant(
    conn: &Connection,
    actor: &AuthorizationAdministrationActor<'_>,
    grant_id: i64,
) -> AdministrationResult<Option<GrantDeactivateOutcome>> {
    require_schema_current(conn)?;
    let actor = normalize_actor(actor)?;
    let tx = conn.unchecked_transaction()?;
    let outcome = deactivate_delegated_grant_in_tx(&tx, &actor, grant_id)?;
    tx.commit()?;
    Ok(outcome)
}

pub fn deactivate_delegated_grant_authorized(
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
    let outcome = deactivate_delegated_grant_in_tx(&tx, &actor, grant_id)?;
    tx.commit()?;
    Ok(outcome)
}

'''
admin = admin[:start] + replacement + admin[end:]
ADMIN.write_text(admin)

access = ACCESS.read_text()
old = '''        .route(\n            "/api/authorization-grants/delegated",\n            post(create_delegated_authorization_grant),\n        )\n        .with_state(state)'''
new = '''        .route(\n            "/api/authorization-grants/delegated",\n            post(create_delegated_authorization_grant),\n        )\n        .route(\n            "/api/authorization-grants/delegated/{grant_id}",\n            delete(deactivate_delegated_authorization_grant),\n        )\n        .with_state(state)'''
assert old in access
access = access.replace(old, new, 1)

marker = 'async fn deactivate_durable_authorization_grant(\n'
assert marker in access
handler = r'''async fn deactivate_delegated_authorization_grant(
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
        authorization_admin::deactivate_delegated_grant_authorized(
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
                "store": "delegated",
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
marker = '''#[tokio::test]\nasync fn authorization_policy_http_is_privileged_canonical_and_observationally_read_only() {'''
assert marker in tests
http_test = r'''#[tokio::test]
async fn authorization_admin_rest_delegated_deactivate_is_privileged_idempotent_and_preserves_scope() {
    let fixture = fixture("authorization-admin-delegated-deactivate");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let conn = db::connect(&fixture.db_path).unwrap();
    let local = authorization_admin::AuthorizationAdministrationActor::local_cli();
    let now: i64 = conn.query_row("SELECT unixepoch()", [], |row| row.get(0)).unwrap();
    let seed = authorization_admin::DelegatedGrantCreateRequest {
        principal_provider: "oidc:https://issuer.example",
        principal_subject: "delegated-deactivate-worker",
        participant_id: &fixture.participant_id,
        capability: authorization::POST_MESSAGE,
        resource: Some("control-systems"),
        intent_id: Some("intent-delegated-deactivate-108"),
        expires_at: Some(now + 3600),
        one_shot: true,
    };
    let created = authorization_admin::create_delegated_grant(&conn, &local, &seed).unwrap();
    drop(conn);
    let uri = format!("/api/authorization-grants/delegated/{}", created.id);

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
    assert_eq!(body["grant"]["store"], "delegated");
    assert_eq!(body["grant"]["id"], created.id);
    assert_eq!(body["grant"]["state"], "inactive");
    assert_eq!(body["grant"]["rows_changed"], 1);

    let conn = db::connect(&fixture.db_path).unwrap();
    let grant_status: String = conn
        .query_row(
            "SELECT status FROM delegated_grants WHERE id = ?1",
            [created.id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(grant_status, "inactive");
    let actor: (String, String, String, String) = conn
        .query_row(
            "SELECT actor_surface, actor_provider, actor_subject, actor_participant_id
             FROM authorization_admin_events
             WHERE grant_store = 'delegated' AND grant_id = ?1 AND operation = 'deactivate'",
            [created.id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .unwrap();
    assert_eq!(actor.0, "rest-authorization-admin");
    assert_eq!(actor.1, "human-web");
    assert_eq!(actor.2, fixture.participant_id);
    assert_eq!(actor.3, fixture.participant_id);
    let scope: (Option<String>, Option<String>, Option<i64>, i64) = conn
        .query_row(
            "SELECT resource, intent_id, expires_at, one_shot
             FROM authorization_admin_events
             WHERE grant_store = 'delegated' AND grant_id = ?1 AND operation = 'deactivate'",
            [created.id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .unwrap();
    assert_eq!(scope.0.as_deref(), Some("control-systems"));
    assert_eq!(scope.1.as_deref(), Some("intent-delegated-deactivate-108"));
    assert_eq!(scope.2, Some(now + 3600));
    assert_eq!(scope.3, 1);
    let before_repeat: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM authorization_admin_events
             WHERE grant_store = 'delegated' AND grant_id = ?1 AND operation = 'deactivate'",
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
             WHERE grant_store = 'delegated' AND grant_id = ?1 AND operation = 'deactivate'",
            [created.id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(after_repeat, before_repeat);
    drop(conn);

    let missing = request(
        &router,
        Method::DELETE,
        "/api/authorization-grants/delegated/9223372036854770000",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(missing.status(), StatusCode::NOT_FOUND);
    let invalid = request(
        &router,
        Method::DELETE,
        "/api/authorization-grants/delegated/0",
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
    assert_eq!(admin_table, 0, "REST delegated deactivate repaired stale schema");
}

'''
tests = tests.replace(marker, http_test + marker, 1)
TESTS.write_text(tests)
