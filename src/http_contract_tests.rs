use std::path::PathBuf;

use axum::{
    body::{to_bytes, Body},
    http::{HeaderMap, Request, StatusCode},
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
async fn navigation_read_preserves_authoritative_fields() {
    let fixture = fixture("single");
    let conn = db::connect(&fixture.db_path).unwrap();
    let row = db::append_message(
        &conn,
        &fixture.identity,
        "control-systems",
        "insight",
        "hello",
        None,
    )
    .unwrap();
    drop(conn);
    let response = get(&fixture.router, "/r/control-systems?after=0&limit=10").await;
    let (status, _, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    let message = message_json(&body);
    assert_eq!(message["id"].as_i64(), Some(row.id));
    assert_eq!(message["source"], "single");
    assert_eq!(message["instance"], "single-main");
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
        signed_auth::SIGNATURE_SCHEME, rotary_signature
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
