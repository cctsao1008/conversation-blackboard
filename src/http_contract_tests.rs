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
    db,
    http::{self, AppState},
    identity,
    model::Identity,
    signed_auth, web_auth,
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
    let (signing_private, signing_public) = signed_auth::generate_keypair();
    identity::set_web_participant_signing_key(&conn, &participant_id, &signing_public).unwrap();
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
    router.clone().oneshot(builder.body(body).unwrap()).await.unwrap()
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
    let signature = signed_auth::sign_write(
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
        "/w/{}?scheme={}&sig={}&channel={}&kind={}&body={}&nonce={}",
        fixture.participant_id,
        signed_auth::SIGNATURE_SCHEME,
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

    let guest = request(
        &fixture.router,
        Method::POST,
        "/api/auth/guest",
        None,
        None,
    )
    .await;
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
async fn signed_navigation_write_is_idempotent_and_raw_key_auth_is_retired() {
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
async fn signed_navigation_write_validates_reply_targets_and_nonce_conflicts() {
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
async fn distinct_signed_participants_keep_provenance_separate() {
    let single = fixture("single");
    let conn = db::connect(&single.db_path).unwrap();
    identity::provision_web_participant_identity(&conn, "rotary-main", "rotary", Some("Rotary"))
        .unwrap()
        .unwrap();
    let (rotary_private, rotary_public) = signed_auth::generate_keypair();
    identity::set_web_participant_signing_key(&conn, "rotary-main", &rotary_public).unwrap();
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

    let rotary_signature = signed_auth::sign_write(
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
        "/w/rotary-main?scheme={}&sig={}&channel=general&kind=message&body=from-rotary&nonce=rotary-001",
        signed_auth::SIGNATURE_SCHEME,
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
