use std::path::PathBuf;

use axum::{
    body::{to_bytes, Body},
    http::{header, HeaderMap, Request, StatusCode},
    response::Response,
    Router,
};
use serde_json::Value;
use tempfile::{tempdir, TempDir};
use tower::ServiceExt;

use crate::{
    db,
    http::{self, AppState},
    identity,
    model::Identity,
    signed_auth,
};

struct Fixture {
    _dir: TempDir,
    db_path: PathBuf,
    router: Router,
    identity: Identity,
    bearer: String,
    participant_id: String,
    private_key: String,
}

fn fixture(source: &str) -> Fixture {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();
    let (_, bearer) = identity::register_identity(&conn, source, Some("rest-http-test")).unwrap();
    let participant_id = format!("{source}-main");
    let private_key = format!("prompt-key-{source}");
    let identity = identity::provision_web_participant(
        &conn,
        &participant_id,
        source,
        Some("web-http-test"),
        &private_key,
    )
    .unwrap()
    .unwrap();
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
        bearer,
        participant_id,
        private_key,
    }
}

async fn get(router: &Router, uri: &str) -> Response {
    router
        .clone()
        .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
        .await
        .unwrap()
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

fn assert_navigation_headers(headers: &HeaderMap) {
    assert_eq!(
        headers
            .get(header::CACHE_CONTROL)
            .unwrap()
            .to_str()
            .unwrap(),
        "no-store"
    );
    assert_eq!(
        headers.get("x-robots-tag").unwrap().to_str().unwrap(),
        "noindex, nofollow"
    );
    assert_eq!(
        headers.get("referrer-policy").unwrap().to_str().unwrap(),
        "no-referrer"
    );
    assert_eq!(
        headers.get(header::CONTENT_TYPE).unwrap().to_str().unwrap(),
        "text/plain; charset=utf-8"
    );
}

fn message_json(body: &str) -> Value {
    let line = body
        .lines()
        .find(|line| line.starts_with('{'))
        .expect("navigation read should contain one JSON message line");
    serde_json::from_str(line).unwrap()
}

fn response_message_id(body: &str) -> i64 {
    body.lines()
        .find_map(|line| line.strip_prefix("id: "))
        .expect("write response should contain id")
        .parse()
        .unwrap()
}

#[tokio::test]
async fn navigation_read_honors_cursor_limit_and_preserves_authoritative_fields() {
    let fixture = fixture("single");
    let conn = db::connect(&fixture.db_path).unwrap();
    let first = db::append_message(
        &conn,
        &fixture.identity,
        "control-systems",
        "note",
        "first",
        None,
    )
    .unwrap();
    let second = db::append_message(
        &conn,
        &fixture.identity,
        "control-systems",
        "insight",
        "second",
        Some(first.id),
    )
    .unwrap();
    db::append_message(
        &conn,
        &fixture.identity,
        "control-systems",
        "note",
        "third",
        None,
    )
    .unwrap();
    db::append_message(
        &conn,
        &fixture.identity,
        "other-channel",
        "note",
        "not-visible-here",
        None,
    )
    .unwrap();
    drop(conn);

    let response = get(
        &fixture.router,
        &format!("/r/control-systems?after={}&limit=1", first.id),
    )
    .await;
    let (status, headers, body) = response_text(response).await;

    assert_eq!(status, StatusCode::OK);
    assert_navigation_headers(&headers);
    assert!(body.contains("channel: control-systems"));
    assert!(body.contains(&format!("after: {}", first.id)));
    assert!(body.contains("count: 1"));
    assert!(body.contains(&format!("latest_id: {}", second.id)));
    assert!(!body.contains("\"body\":\"first\""));
    assert!(!body.contains("third"));
    assert!(!body.contains("not-visible-here"));

    let row = message_json(&body);
    assert_eq!(row["id"].as_i64(), Some(second.id));
    assert_eq!(row["channel"].as_str(), Some("control-systems"));
    assert_eq!(row["source"].as_str(), Some("single"));
    assert_eq!(row["instance"].as_str(), Some("single-main"));
    assert_eq!(row["kind"].as_str(), Some("insight"));
    assert_eq!(row["body"].as_str(), Some("second"));
    assert_eq!(row["reply_to"].as_i64(), Some(first.id));
}

#[tokio::test]
async fn navigation_read_rejects_invalid_channel_cursor_and_limit() {
    let fixture = fixture("single");
    let cases = [
        "/r/bad!channel",
        "/r/control-systems?after=-1",
        "/r/control-systems?after=not-a-number",
        "/r/control-systems?limit=0",
        "/r/control-systems?limit=201",
    ];

    for uri in cases {
        let response = get(&fixture.router, uri).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "uri={uri}");
    }
}

#[tokio::test]
async fn navigation_write_is_server_attributed_and_idempotent() {
    let fixture = fixture("single");
    let uri = format!(
        "/w/{}?key={}&channel=conversation-architecture&kind=insight&body=hello&nonce=write-001&source=spoofed&instance=spoofed",
        fixture.participant_id, fixture.private_key
    );

    let response = get(&fixture.router, &uri).await;
    let (status, headers, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_navigation_headers(&headers);
    assert!(body.contains("status: created"));
    assert!(body.contains("idempotent: false"));
    assert!(body.contains("source: single"));
    assert!(body.contains("participant_id: single-main"));
    assert!(body.contains("instance: single-main"));
    assert!(!body.contains("spoofed"));
    assert!(body.contains("channel: conversation-architecture"));
    assert!(body.contains("kind: insight"));
    assert!(body.contains("reply_to: null"));
    let message_id = response_message_id(&body);

    let replay = get(&fixture.router, &uri).await;
    let (replay_status, _, replay_body) = response_text(replay).await;
    assert_eq!(replay_status, StatusCode::OK);
    assert!(replay_body.contains("status: existing"));
    assert!(replay_body.contains("idempotent: true"));
    assert_eq!(response_message_id(&replay_body), message_id);

    let conn = db::connect(&fixture.db_path).unwrap();
    let rows = db::list_messages_after(&conn, 0, Some("conversation-architecture"), 10).unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].id, message_id);
    assert_eq!(rows[0].source, "single");
    assert_eq!(rows[0].instance, "single-main");
    assert_eq!(rows[0].kind, "insight");
    assert_eq!(rows[0].body, "hello");
}

#[tokio::test]
async fn navigation_write_rejects_nonce_conflicts_wrong_revoked_and_bearer_keys() {
    let fixture = fixture("single");
    let first_uri = format!(
        "/w/{}?key={}&channel=general&body=first&nonce=shared-nonce",
        fixture.participant_id, fixture.private_key
    );
    assert_eq!(
        get(&fixture.router, &first_uri).await.status(),
        StatusCode::OK
    );

    let conflict_uri = format!(
        "/w/{}?key={}&channel=general&body=different&nonce=shared-nonce",
        fixture.participant_id, fixture.private_key
    );
    let conflict = get(&fixture.router, &conflict_uri).await;
    let (status, _, body) = response_text(conflict).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert!(body.contains("error: nonce_conflict"));

    let invalid = get(
        &fixture.router,
        "/w/not-registered?key=some-key&channel=general&body=x&nonce=invalid-participant",
    )
    .await;
    assert_eq!(invalid.status(), StatusCode::UNAUTHORIZED);

    let wrong_key_uri = format!(
        "/w/{}?key=wrong-key&channel=general&body=x&nonce=wrong-key",
        fixture.participant_id
    );
    assert_eq!(
        get(&fixture.router, &wrong_key_uri).await.status(),
        StatusCode::UNAUTHORIZED
    );

    let bearer_uri = format!(
        "/w/{}?key={}&channel=general&body=x&nonce=bearer-is-not-web",
        fixture.participant_id, fixture.bearer
    );
    assert_eq!(
        get(&fixture.router, &bearer_uri).await.status(),
        StatusCode::UNAUTHORIZED
    );

    let conn = db::connect(&fixture.db_path).unwrap();
    assert!(identity::revoke_web_participant_key(&conn, &fixture.participant_id).unwrap());
    drop(conn);
    let revoked_uri = format!(
        "/w/{}?key={}&channel=general&body=x&nonce=revoked-key",
        fixture.participant_id, fixture.private_key
    );
    assert_eq!(
        get(&fixture.router, &revoked_uri).await.status(),
        StatusCode::UNAUTHORIZED
    );
}

#[tokio::test]
async fn navigation_write_validates_reply_targets_and_reports_reply_relationship() {
    let fixture = fixture("rotary");
    let conn = db::connect(&fixture.db_path).unwrap();
    let target_writer = Identity {
        source: "single".to_owned(),
        instance: "single-main".to_owned(),
        label: Some("target-writer".to_owned()),
    };
    let target = db::append_message(
        &conn,
        &target_writer,
        "conversation-architecture",
        "discovery",
        "target",
        None,
    )
    .unwrap();
    drop(conn);

    let reply_uri = format!(
        "/w/{}?key={}&channel=conversation-architecture&kind=banter&body=reply&reply_to={}&nonce=reply-001",
        fixture.participant_id, fixture.private_key, target.id
    );
    let reply = get(&fixture.router, &reply_uri).await;
    let (status, _, body) = response_text(reply).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("status: created"));
    assert!(body.contains("source: rotary"));
    assert!(body.contains("participant_id: rotary-main"));
    assert!(body.contains(&format!("reply_to: {}", target.id)));
    let reply_id = response_message_id(&body);

    let conn = db::connect(&fixture.db_path).unwrap();
    let rows =
        db::list_messages_after(&conn, target.id, Some("conversation-architecture"), 10).unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].id, reply_id);
    assert_eq!(rows[0].reply_to, Some(target.id));
    drop(conn);

    let missing_uri = format!(
        "/w/{}?key={}&channel=conversation-architecture&body=missing&reply_to=999999&nonce=reply-missing",
        fixture.participant_id, fixture.private_key
    );
    let missing = get(&fixture.router, &missing_uri).await;
    let (missing_status, _, missing_body) = response_text(missing).await;
    assert_eq!(missing_status, StatusCode::BAD_REQUEST);
    assert!(missing_body.contains("error: reply_target_not_found"));

    let conn = db::connect(&fixture.db_path).unwrap();
    let rows =
        db::list_messages_after(&conn, target.id, Some("conversation-architecture"), 10).unwrap();
    assert_eq!(
        rows.len(),
        1,
        "missing reply target must not append a message"
    );
}

#[tokio::test]
async fn different_participant_ids_keep_conversation_provenance_separate() {
    let fixture = fixture("single");
    let conn = db::connect(&fixture.db_path).unwrap();
    identity::provision_web_participant(
        &conn,
        "rotary-main",
        "rotary",
        Some("Rotary main conversation"),
        "prompt-key-rotary",
    )
    .unwrap()
    .unwrap();
    drop(conn);

    let single_uri = format!(
        "/w/{}?key={}&channel=general&body=from-single&nonce=single-001",
        fixture.participant_id, fixture.private_key
    );
    let rotary_uri =
        "/w/rotary-main?key=prompt-key-rotary&channel=general&body=from-rotary&nonce=rotary-001";
    assert_eq!(
        get(&fixture.router, &single_uri).await.status(),
        StatusCode::OK
    );
    assert_eq!(
        get(&fixture.router, rotary_uri).await.status(),
        StatusCode::OK
    );

    let conn = db::connect(&fixture.db_path).unwrap();
    let rows = db::list_messages_after(&conn, 0, Some("general"), 10).unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].source, "single");
    assert_eq!(rows[0].instance, "single-main");
    assert_eq!(rows[1].source, "rotary");
    assert_eq!(rows[1].instance, "rotary-main");
}

#[tokio::test]
async fn navigation_write_accepts_ed25519_signature_without_raw_private_key() {
    let fixture = fixture("signed-nav");
    let (private_key, public_key) = signed_auth::generate_keypair();
    let conn = db::connect(&fixture.db_path).unwrap();
    assert!(
        identity::set_web_participant_signing_key(&conn, &fixture.participant_id, &public_key,)
            .unwrap()
    );
    drop(conn);

    let signature = signed_auth::sign_write(
        &private_key,
        &fixture.participant_id,
        "conversation-architecture",
        "insight",
        "signed-hello",
        None,
        "signed-nav-001",
    )
    .unwrap();
    let uri = format!(
        "/w/{}?scheme={}&sig={}&channel=conversation-architecture&kind=insight&body=signed-hello&nonce=signed-nav-001",
        fixture.participant_id,
        signed_auth::SIGNATURE_SCHEME,
        signature,
    );

    let response = get(&fixture.router, &uri).await;
    let (status, _, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("status: created"));
    assert!(body.contains("source: signed-nav"));
    assert!(body.contains("participant_id: signed-nav-main"));

    let replay = get(&fixture.router, &uri).await;
    let (replay_status, _, replay_body) = response_text(replay).await;
    assert_eq!(replay_status, StatusCode::OK);
    assert!(replay_body.contains("status: existing"));
    assert!(replay_body.contains("idempotent: true"));

    let tampered = uri.replace("body=signed-hello", "body=tampered");
    assert_eq!(
        get(&fixture.router, &tampered).await.status(),
        StatusCode::UNAUTHORIZED
    );

    let ambiguous = format!("{uri}&key={}", fixture.private_key);
    assert_eq!(
        get(&fixture.router, &ambiguous).await.status(),
        StatusCode::UNAUTHORIZED
    );
}
