use std::path::PathBuf;

use axum::{
    body::{to_bytes, Body},
    http::{header, HeaderMap, Method, Request, StatusCode},
    response::Response,
    Router,
};
use serde_json::{json, Value};
use tempfile::{tempdir, TempDir};
use tower::ServiceExt;

use crate::{
    authorization, authorization_admin, db, execution,
    http::{self, AppState},
    identity,
    model::Identity,
    participant_auth, request_auth, web_auth,
};

struct Fixture {
    _dir: TempDir,
    db_path: PathBuf,
    router: Router,
    identity: Identity,
    participant_id: String,
    signing_private: String,
}

fn fixture(source: &str) -> Fixture {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();
    let participant_id = format!("{source}-main");
    let identity = identity::provision_web_participant_identity(
        &conn,
        &participant_id,
        source,
        Some("http-test"),
    )
    .unwrap()
    .unwrap();
    let (signing_private, signing_public) = participant_auth::generate_secret();
    identity::set_web_participant_auth_secret(&conn, &participant_id, &signing_public).unwrap();
    drop(conn);
    let router = http::app(AppState {
        db_path: db_path.clone(),
        registration_key: None,
    });
    Fixture {
        _dir: dir,
        db_path,
        router,
        identity,
        participant_id,
        signing_private,
    }
}

async fn request(
    router: &Router,
    method: Method,
    uri: &str,
    session: Option<&str>,
    body: Option<Value>,
) -> Response {
    let mut builder = Request::builder().method(method).uri(uri);
    if let Some(session) = session {
        builder = builder.header(web_auth::WEB_SESSION_HEADER, session);
    }
    let body = match body {
        Some(value) => {
            builder = builder.header(header::CONTENT_TYPE, "application/json");
            Body::from(value.to_string())
        }
        None => Body::empty(),
    };
    router
        .clone()
        .oneshot(builder.body(body).unwrap())
        .await
        .unwrap()
}

async fn get(router: &Router, uri: &str) -> Response {
    request(router, Method::GET, uri, None, None).await
}

async fn response_text(response: Response) -> (StatusCode, HeaderMap, String) {
    let (parts, body) = response.into_parts();
    let bytes = to_bytes(body, http::MAX_BODY_BYTES * 2).await.unwrap();
    (
        parts.status,
        parts.headers,
        String::from_utf8(bytes.to_vec()).unwrap(),
    )
}

async fn response_json(response: Response) -> (StatusCode, Value) {
    let status = response.status();
    let bytes = to_bytes(response.into_body(), http::MAX_BODY_BYTES * 2)
        .await
        .unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

fn message_json(body: &str) -> Value {
    let line = body.lines().find(|line| line.starts_with('{')).unwrap();
    serde_json::from_str(line).unwrap()
}

fn response_message_id(body: &str) -> i64 {
    body.lines()
        .find_map(|line| line.strip_prefix("id: "))
        .unwrap()
        .parse()
        .unwrap()
}

fn signed_uri(
    fixture: &Fixture,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> String {
    let signature = participant_auth::compute_write_proof(
        &fixture.signing_private,
        &fixture.participant_id,
        channel,
        kind,
        body,
        reply_to,
        nonce,
    )
    .unwrap();
    let mut uri = format!(
        "/w/{}?scheme={}&proof={}&channel={}&kind={}&body={}&nonce={}",
        fixture.participant_id,
        participant_auth::AUTH_SCHEME,
        signature,
        channel,
        kind,
        body,
        nonce,
    );
    if let Some(reply_to) = reply_to {
        uri.push_str(&format!("&reply_to={reply_to}"));
    }
    uri
}

#[tokio::test]
async fn anonymous_navigation_read_exposes_only_public_active_channels() {
    let fixture = fixture("single");
    let conn = db::connect(&fixture.db_path).unwrap();
    let public = db::append_message(
        &conn,
        &fixture.identity,
        "blackboard-lounge",
        "insight",
        "hello public",
        None,
    )
    .unwrap();
    db::append_message(
        &conn,
        &fixture.identity,
        "control-systems",
        "insight",
        "hello private",
        None,
    )
    .unwrap();
    drop(conn);

    let response = get(&fixture.router, "/r/blackboard-lounge?after=0&limit=10").await;
    let (status, _, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    let message = message_json(&body);
    assert_eq!(message["id"].as_i64(), Some(public.id));
    assert_eq!(message["source"], "single");
    assert_eq!(message["instance"], "single-main");

    assert_eq!(
        get(&fixture.router, "/r/control-systems?after=0&limit=10")
            .await
            .status(),
        StatusCode::NOT_FOUND
    );
}

#[tokio::test]
async fn guest_session_reads_public_channels_only_and_cannot_write() {
    let fixture = fixture("single");
    let conn = db::connect(&fixture.db_path).unwrap();
    db::append_message(
        &conn,
        &fixture.identity,
        "blackboard-lounge",
        "message",
        "public",
        None,
    )
    .unwrap();
    db::append_message(
        &conn,
        &fixture.identity,
        "control-systems",
        "message",
        "private",
        None,
    )
    .unwrap();
    drop(conn);

    let guest = request(&fixture.router, Method::POST, "/api/auth/guest", None, None).await;
    let (status, guest_body) = response_json(guest).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(guest_body["instance"], "anonymous");
    assert_eq!(guest_body["role"], "guest");
    let token = guest_body["session_token"].as_str().unwrap();

    let channels = request(
        &fixture.router,
        Method::GET,
        "/api/channels",
        Some(token),
        None,
    )
    .await;
    let (status, body) = response_json(channels).await;
    assert_eq!(status, StatusCode::OK);
    let rows = body["channels"].as_array().unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["channel"], "blackboard-lounge");
    assert_eq!(rows[0]["visibility"], "public");

    assert_eq!(
        request(
            &fixture.router,
            Method::GET,
            "/api/messages?channel=blackboard-lounge",
            Some(token),
            None,
        )
        .await
        .status(),
        StatusCode::OK
    );
    assert_eq!(
        request(
            &fixture.router,
            Method::GET,
            "/api/messages?channel=control-systems",
            Some(token),
            None,
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        request(
            &fixture.router,
            Method::POST,
            "/api/messages",
            Some(token),
            Some(json!({"channel": "blackboard-lounge", "body": "nope"})),
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
}

#[tokio::test]
async fn human_admin_can_manage_channels_while_normal_human_cannot() {
    let admin = fixture("cheng");
    let conn = db::connect(&admin.db_path).unwrap();
    identity::set_web_participant_role(&conn, "cheng-main", "admin").unwrap();
    drop(conn);
    let admin_session = web_auth::issue_web_session("cheng-main");

    assert_eq!(
        request(
            &admin.router,
            Method::GET,
            "/api/admin/channels",
            Some(&admin_session.token),
            None,
        )
        .await
        .status(),
        StatusCode::OK
    );
    let created = request(
        &admin.router,
        Method::POST,
        "/api/admin/channels",
        Some(&admin_session.token),
        Some(json!({"name": "public-demo", "visibility": "public"})),
    )
    .await;
    let (status, body) = response_json(created).await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body["channel"]["visibility"], "public");
    assert_eq!(body["channel"]["status"], "active");

    let patched = request(
        &admin.router,
        Method::PATCH,
        "/api/admin/channels/public-demo",
        Some(&admin_session.token),
        Some(json!({"visibility": "private", "status": "archived"})),
    )
    .await;
    let (status, body) = response_json(patched).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["channel"]["visibility"], "private");
    assert_eq!(body["channel"]["status"], "archived");

    let user = fixture("single");
    let user_session = web_auth::issue_web_session("single-main");
    assert_eq!(
        request(
            &user.router,
            Method::GET,
            "/api/admin/channels",
            Some(&user_session.token),
            None,
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
}

#[tokio::test]
async fn hmac_navigation_write_is_idempotent_and_raw_key_auth_is_retired() {
    let fixture = fixture("single");
    let uri = signed_uri(&fixture, "general", "insight", "hello", None, "nav-001");
    let response = get(&fixture.router, &uri).await;
    let (status, _, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("status: created"));
    let id = response_message_id(&body);
    let replay = get(&fixture.router, &uri).await;
    let (_, _, replay_body) = response_text(replay).await;
    assert!(replay_body.contains("status: existing"));
    assert_eq!(response_message_id(&replay_body), id);

    let tampered = uri.replace("body=hello", "body=tampered");
    assert_eq!(
        get(&fixture.router, &tampered).await.status(),
        StatusCode::UNAUTHORIZED
    );
    let legacy = format!(
        "/w/{}?key=retired&channel=general&body=x&nonce=legacy",
        fixture.participant_id
    );
    assert_eq!(
        get(&fixture.router, &legacy).await.status(),
        StatusCode::UNAUTHORIZED
    );
}

#[tokio::test]
async fn hmac_navigation_write_validates_reply_targets_and_nonce_conflicts() {
    let fixture = fixture("rotary");
    let conn = db::connect(&fixture.db_path).unwrap();
    let target_writer = Identity {
        source: "single".into(),
        instance: "single-main".into(),
        label: None,
    };
    let target = db::append_message(
        &conn,
        &target_writer,
        "conversation-architecture",
        "idea",
        "target",
        None,
    )
    .unwrap();
    drop(conn);

    let reply = signed_uri(
        &fixture,
        "conversation-architecture",
        "insight",
        "reply",
        Some(target.id),
        "reply-001",
    );
    let response = get(&fixture.router, &reply).await;
    let (status, _, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains(&format!("reply_to: {}", target.id)));

    let first = signed_uri(
        &fixture,
        "general",
        "message",
        "first",
        None,
        "shared-nonce",
    );
    assert_eq!(get(&fixture.router, &first).await.status(), StatusCode::OK);
    let conflict = signed_uri(
        &fixture,
        "general",
        "message",
        "different",
        None,
        "shared-nonce",
    );
    let (status, _, body) = response_text(get(&fixture.router, &conflict).await).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert!(body.contains("nonce_conflict"));

    let missing = signed_uri(
        &fixture,
        "conversation-architecture",
        "message",
        "missing",
        Some(999999),
        "missing-reply",
    );
    let (status, _, body) = response_text(get(&fixture.router, &missing).await).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains("reply_target_not_found"));
}

#[tokio::test]
async fn distinct_hmac_participants_keep_provenance_separate() {
    let single = fixture("single");
    let conn = db::connect(&single.db_path).unwrap();
    identity::provision_web_participant_identity(&conn, "rotary-main", "rotary", Some("Rotary"))
        .unwrap()
        .unwrap();
    let (rotary_private, rotary_public) = participant_auth::generate_secret();
    identity::set_web_participant_auth_secret(&conn, "rotary-main", &rotary_public).unwrap();
    drop(conn);

    let single_uri = signed_uri(
        &single,
        "general",
        "message",
        "from-single",
        None,
        "single-001",
    );
    assert_eq!(
        get(&single.router, &single_uri).await.status(),
        StatusCode::OK
    );

    let rotary_signature = participant_auth::compute_write_proof(
        &rotary_private,
        "rotary-main",
        "general",
        "message",
        "from-rotary",
        None,
        "rotary-001",
    )
    .unwrap();
    let rotary_uri = format!(
        "/w/rotary-main?scheme={}&proof={}&channel=general&kind=message&body=from-rotary&nonce=rotary-001",
        participant_auth::AUTH_SCHEME,
        rotary_signature
    );
    assert_eq!(
        get(&single.router, &rotary_uri).await.status(),
        StatusCode::OK
    );

    let conn = db::connect(&single.db_path).unwrap();
    let rows = db::list_messages_after(&conn, 0, Some("general"), 10).unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].instance, "single-main");
    assert_eq!(rows[1].instance, "rotary-main");
}

#[tokio::test]
async fn execution_audit_http_is_policy_guarded_and_reads_committed_evidence() {
    let fixture = fixture("audit");
    let conn = db::connect(&fixture.db_path).unwrap();
    execution::migrate_execution_schema(&conn).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    let seed = db::append_message(
        &conn,
        &fixture.identity,
        "blackboard-lounge",
        "message",
        "audit seed",
        None,
    )
    .unwrap();
    assert_eq!(seed.id, 1);
    let intent_hash =
        execution::message_request_hash("blackboard-lounge", "message", "audit seed", None, None);
    conn.execute(
        "INSERT INTO execution_receipts
            (participant_id, intent_id, intent_hash, capability, message_id, status)
         VALUES (?1, 'intent-http-audit', ?2, 'post_message', 1, 'committed')",
        rusqlite::params![&fixture.participant_id, &intent_hash],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)
         VALUES (?1, 'intent-http-audit', ?2, 1)",
        rusqlite::params![&fixture.participant_id, &intent_hash],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO execution_authorization_provenance
            (participant_id, intent_id, principal_provider, principal_subject,
             capability, resource, source, reason, grant_id)
         VALUES (?1, 'intent-http-audit', 'human-web', ?1,
                 'post_message', 'blackboard-lounge',
                 'implicit_authority', 'implicit_human_web', NULL)",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO ingress_provenance
            (delivery_id, participant_id, intent_id, transport, external_ref,
             principal_provider, principal_subject)
         VALUES ('delivery-http-audit', ?1, 'intent-http-audit', 'rest', 'ref', 'human-web', ?1)",
        [&fixture.participant_id],
    )
    .unwrap();
    assert!(execution::get_execution_audit_bundle(
        &conn,
        &fixture.participant_id,
        "intent-http-audit",
    )
    .unwrap()
    .is_some());
    drop(conn);

    let audit_router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let session = web_auth::issue_web_session(&fixture.participant_id);
    let receipt_probe = request(
        &audit_router,
        Method::GET,
        "/api/executions/intent-http-audit",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(receipt_probe.status(), StatusCode::OK);
    let response = request(
        &audit_router,
        Method::GET,
        "/api/executions/intent-http-audit/audit",
        Some(&session.token),
        None,
    )
    .await;
    let (status, body) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["audit"]["receipt"]["intent_id"], "intent-http-audit");
    assert_eq!(
        body["audit"]["authorization"]["reason"],
        "implicit_human_web"
    );
    assert_eq!(body["audit"]["ingress"].as_array().unwrap().len(), 1);

    let integrity = request(
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
        &audit_router,
        Method::GET,
        "/api/executions/intent-http-audit/audit",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);
    let integrity_denied = request(
        &audit_router,
        Method::GET,
        "/api/executions/intent-http-audit/audit/integrity",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(integrity_denied.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn execution_audit_sweep_http_requires_privileged_authority_and_returns_invalid_data() {
    let fixture = fixture("sweep");
    let audit_router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let session = web_auth::issue_web_session(&fixture.participant_id);

    let ordinary = request(
        &audit_router,
        Method::GET,
        "/api/execution-audit/sweep",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "UPDATE web_participants SET role = 'admin' WHERE participant_id = ?1",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);

    let clean = request(
        &audit_router,
        Method::GET,
        "/api/execution-audit/sweep",
        Some(&session.token),
        None,
    )
    .await;
    let (clean_status, clean_body) = response_json(clean).await;
    assert_eq!(clean_status, StatusCode::OK);
    assert_eq!(clean_body["sweep"]["valid"], true);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)
         VALUES ('ghost-main', 'orphan-http-sweep', 'hash', 99)",
        [],
    )
    .unwrap();
    drop(conn);

    let invalid = request(
        &audit_router,
        Method::GET,
        "/api/execution-audit/sweep",
        Some(&session.token),
        None,
    )
    .await;
    let (invalid_status, invalid_body) = response_json(invalid).await;
    assert_eq!(invalid_status, StatusCode::OK);
    assert_eq!(invalid_body["sweep"]["valid"], false);
    assert!(invalid_body["sweep"]["orphan_evidence"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| entry["kind"] == "orphan_navigation_reservation"));
}

#[tokio::test]
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
    let proof =
        participant_auth::compute_message_proof(&fixture.signing_private, &canonical).unwrap();
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

    let existing = request(&router, Method::POST, uri, Some(&session.token), Some(body)).await;
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
    assert_eq!(
        after_count, event_count,
        "idempotent create emitted a false event"
    );
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

    let allowed = router.clone().oneshot(bearer_request(body)).await.unwrap();
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
    conn.execute("DROP TABLE authorization_admin_events", [])
        .unwrap();
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

#[tokio::test]
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
    let proof =
        participant_auth::compute_message_proof(&fixture.signing_private, &canonical).unwrap();
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
    conn.execute("DROP TABLE authorization_admin_events", [])
        .unwrap();
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

#[tokio::test]
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
    assert_eq!(
        after, 0,
        "read-only policy GET recreated authorization schema"
    );
}

#[tokio::test]
async fn authorization_decision_http_is_privileged_query_bound_and_read_only() {
    let fixture = fixture("decision-http");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let target_query = "/api/authorization-decision/explain?principal_provider=oidc%3Ahttps%3A%2F%2Fissuer.example&principal_subject=target-agent&participant_id=decision-http-main&capability=post_message&resource=alpha";

    let unauthenticated = request(&router, Method::GET, target_query, None, None).await;
    assert_eq!(unauthenticated.status(), StatusCode::UNAUTHORIZED);

    let session = web_auth::issue_web_session(&fixture.participant_id);
    let ordinary = request(
        &router,
        Method::GET,
        target_query,
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    // Snapshot authority is not decision-explain authority.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('human-web', ?1, ?1, 'read_authorization_policy', 'authorization-policy')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('oidc:https://issuer.example', 'target-agent', ?1, 'post_message', 'alpha')",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);
    let wrong_capability = request(
        &router,
        Method::GET,
        target_query,
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

    // Caller is Human Web admin while the target is a distinct OIDC principal.
    let allowed = request(
        &router,
        Method::GET,
        target_query,
        Some(&session.token),
        None,
    )
    .await;
    let (allowed_status, allowed_body) = response_json(allowed).await;
    assert_eq!(allowed_status, StatusCode::OK);
    assert_eq!(allowed_body["decision"]["allowed"], true);
    assert_eq!(allowed_body["decision"]["source"], "principal_grants");
    assert_eq!(
        allowed_body["decision"]["reason"],
        "explicit_durable_grant_match"
    );
    assert_eq!(allowed_body["decision"]["consume_on_commit"], false);
    assert!(allowed_body["decision"].get("consume_grant_id").is_none());

    // A target denial is successful explanation data, not transport authorization failure.
    let denied_query = target_query.replace("resource=alpha", "resource=beta");
    let denied = request(
        &router,
        Method::GET,
        &denied_query,
        Some(&session.token),
        None,
    )
    .await;
    let (denied_status, denied_body) = response_json(denied).await;
    assert_eq!(denied_status, StatusCode::OK);
    assert_eq!(denied_body["decision"]["allowed"], false);
    assert_eq!(
        denied_body["decision"]["reason"],
        "explicit_resource_scope_mismatch"
    );

    // Explicit participant-HMAC explain authority succeeds, and its proof binds
    // the exact path+query target. Reusing the proof for a changed resource fails.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', ?1, ?1, 'read_authorization_decision',
                 'authorization-decision')",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);
    let canonical =
        web_auth::canonical_http_request_bytes(&fixture.participant_id, "GET", target_query);
    let proof =
        participant_auth::compute_message_proof(&fixture.signing_private, &canonical).unwrap();
    let hmac_request = |uri: &str| {
        Request::builder()
            .method(Method::GET)
            .uri(uri)
            .header(request_auth::PARTICIPANT_ID_HEADER, &fixture.participant_id)
            .header(
                request_auth::AUTH_SCHEME_HEADER,
                participant_auth::AUTH_SCHEME,
            )
            .header(request_auth::AUTH_PROOF_HEADER, &proof)
            .body(Body::empty())
            .unwrap()
    };
    let hmac_allowed = router
        .clone()
        .oneshot(hmac_request(target_query))
        .await
        .unwrap();
    assert_eq!(hmac_allowed.status(), StatusCode::OK);
    let changed_target = router
        .clone()
        .oneshot(hmac_request(&denied_query))
        .await
        .unwrap();
    assert_eq!(changed_target.status(), StatusCode::UNAUTHORIZED);

    // Invalid target inputs are rejected after caller authentication.
    let invalid = request(
        &router,
        Method::GET,
        "/api/authorization-decision/explain?principal_provider=oidc&principal_subject=target-agent&participant_id=decision-http-main&capability=definitely_unknown",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(invalid.status(), StatusCode::BAD_REQUEST);

    // Legacy authorization state fails without recreating the missing table.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    drop(conn);
    let legacy = request(
        &router,
        Method::GET,
        target_query,
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(legacy.status(), StatusCode::SERVICE_UNAVAILABLE);
    let conn = db::connect(&fixture.db_path).unwrap();
    let table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'delegated_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(
        table_count, 0,
        "decision explain recreated authorization schema"
    );
}

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
    assert_eq!(
        after, 0,
        "read-only integrity GET recreated authorization schema"
    );
}
