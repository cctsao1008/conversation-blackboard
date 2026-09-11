use std::path::PathBuf;

use axum::{
    body::{to_bytes, Body},
    http::{header, Method, Request, StatusCode},
    Router,
};
use serde_json::{json, Value};
use tempfile::{tempdir, TempDir};
use tower::ServiceExt;

use crate::{db, http, identity, signed_auth, web_auth};

struct Fixture {
    _dir: TempDir,
    db_path: PathBuf,
    router: Router,
    participant_id: String,
    private_key: String,
}

fn fixture() -> Fixture {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();
    identity::provision_web_participant(
        &conn,
        "browser-main",
        "browser",
        Some("Browser participant"),
        "legacy-browser-key",
    )
    .unwrap()
    .unwrap();
    let (private_key, public_key) = signed_auth::generate_keypair();
    identity::set_web_participant_signing_key(&conn, "browser-main", &public_key).unwrap();
    drop(conn);

    let router = http::app(http::AppState {
        db_path: db_path.clone(),
        registration_key: None,
    });
    Fixture {
        _dir: dir,
        db_path,
        router,
        participant_id: "browser-main".to_owned(),
        private_key,
    }
}

async fn json_request(
    router: &Router,
    method: Method,
    uri: &str,
    body: Option<Value>,
    headers: &[(&str, String)],
) -> (StatusCode, Value) {
    let mut builder = Request::builder().method(method).uri(uri);
    if body.is_some() {
        builder = builder.header(header::CONTENT_TYPE, "application/json");
    }
    for (name, value) in headers {
        builder = builder.header(*name, value);
    }
    let request_body = body
        .map(|value| Body::from(value.to_string()))
        .unwrap_or_else(Body::empty);
    let response = router
        .clone()
        .oneshot(builder.body(request_body).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 256 * 1024).await.unwrap();
    let value = serde_json::from_slice(&bytes).unwrap_or_else(|_| json!({}));
    (status, value)
}

#[tokio::test]
async fn browser_challenge_activates_local_signer_without_session() {
    let fixture = fixture();
    let (status, challenge) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/challenge",
        Some(json!({"participant_id": fixture.participant_id})),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    let challenge_text = challenge["challenge"].as_str().unwrap();
    let expires_at = challenge["expires_at"].as_u64().unwrap();
    let canonical = web_auth::canonical_browser_challenge_bytes(
        &fixture.participant_id,
        challenge_text,
        expires_at,
    );
    let signature = signed_auth::sign_message_signature(&fixture.private_key, &canonical).unwrap();

    let (status, identity) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/verify",
        Some(json!({
            "participant_id": fixture.participant_id,
            "challenge": challenge_text,
            "expires_at": expires_at,
            "challenge_token": challenge["challenge_token"],
            "auth": {
                "scheme": signed_auth::SIGNATURE_SCHEME,
                "signature": signature,
            }
        })),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(identity["source"], "browser");
    assert_eq!(identity["instance"], "browser-main");
}

#[tokio::test]
async fn signed_browser_reads_bind_exact_request_target() {
    let fixture = fixture();
    let target = "/api/channels";
    let canonical = web_auth::canonical_http_request_bytes(&fixture.participant_id, "GET", target);
    let signature = signed_auth::sign_message_signature(&fixture.private_key, &canonical).unwrap();
    let headers = [
        (
            "X-Blackboard-Participant-Id",
            fixture.participant_id.clone(),
        ),
        (
            "X-Blackboard-Signature-Scheme",
            signed_auth::SIGNATURE_SCHEME.to_owned(),
        ),
        ("X-Blackboard-Signature", signature.clone()),
    ];
    let (status, _) = json_request(&fixture.router, Method::GET, target, None, &headers).await;
    assert_eq!(status, StatusCode::OK);

    let (status, _) = json_request(
        &fixture.router,
        Method::GET,
        "/api/messages?channel=general&after=0&limit=10",
        None,
        &headers,
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn signed_browser_write_is_verified_and_idempotent() {
    let fixture = fixture();
    let participant_id = &fixture.participant_id;
    let channel = "browser-signed";
    let kind = "message";
    let body = "signed in browser";
    let nonce = "web-test-001";
    let signature = signed_auth::sign_write(
        &fixture.private_key,
        participant_id,
        channel,
        kind,
        body,
        None,
        nonce,
    )
    .unwrap();
    let payload = json!({
        "participant_id": participant_id,
        "channel": channel,
        "kind": kind,
        "body": body,
        "reply_to": null,
        "nonce": nonce,
        "auth": {
            "scheme": signed_auth::SIGNATURE_SCHEME,
            "signature": signature,
        }
    });

    let (status, first) = json_request(
        &fixture.router,
        Method::POST,
        "/api/messages",
        Some(payload.clone()),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(first["status"], "created");
    assert_eq!(first["message"]["source"], "browser");
    assert_eq!(first["message"]["instance"], "browser-main");
    let id = first["message"]["id"].as_i64().unwrap();

    let (status, replay) = json_request(
        &fixture.router,
        Method::POST,
        "/api/messages",
        Some(payload),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(replay["status"], "existing");
    assert_eq!(replay["message"]["id"].as_i64(), Some(id));

    let conn = db::connect(&fixture.db_path).unwrap();
    let rows = db::list_messages_after(&conn, 0, Some(channel), 10).unwrap();
    assert_eq!(rows.len(), 1);
}

#[test]
fn browser_bundle_contains_private_key_only_inside_local_container() {
    let bundle = web_auth::credential_bundle("browser-main", "ed25519-sk:test");
    assert!(bundle.starts_with("bbcred-v1:"));
    let app = include_str!("../web/app.js");
    assert!(!app.contains("X-Blackboard-Private-Key"));
    assert!(!app.contains("state.privateKey"));
    assert!(app.contains("crypto.subtle.sign"));
    assert!(app.contains("crypto.subtle.importKey"));
}
