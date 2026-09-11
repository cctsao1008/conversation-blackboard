use std::path::PathBuf;

use axum::{
    body::{to_bytes, Body},
    http::{header, Method, Request, StatusCode},
    Router,
};
use serde_json::{json, Value};
use tempfile::{tempdir, TempDir};
use tower::ServiceExt;

use crate::{db, http, identity, web_auth};

struct Fixture {
    _dir: TempDir,
    db_path: PathBuf,
    router: Router,
    participant_id: String,
    secret: String,
}

fn fixture() -> Fixture {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();
    identity::provision_web_participant_identity(
        &conn,
        "browser-main",
        "browser",
        Some("Browser participant"),
    )
    .unwrap()
    .unwrap();
    let secret = web_auth::generate_totp_secret();
    identity::set_web_participant_totp(&conn, "browser-main", &secret).unwrap();
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
        secret,
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
async fn browser_totp_login_issues_session_for_normal_reads_and_writes() {
    let fixture = fixture();
    let now = web_auth::current_unix_time();
    let code = web_auth::totp_code_at(&fixture.secret, now, web_auth::TOTP_DIGITS).unwrap();
    let (status, auth) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/totp",
        Some(json!({"participant_id": fixture.participant_id, "code": code})),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(auth["source"], "browser");
    assert_eq!(auth["instance"], "browser-main");
    let session = auth["session_token"].as_str().unwrap().to_owned();
    let headers = [(web_auth::WEB_SESSION_HEADER, session)];

    let (status, _) = json_request(
        &fixture.router,
        Method::GET,
        "/api/channels",
        None,
        &headers,
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    let (status, written) = json_request(
        &fixture.router,
        Method::POST,
        "/api/messages",
        Some(json!({
            "channel": "human-web",
            "kind": "message",
            "body": "TOTP-authenticated human write",
            "reply_to": null
        })),
        &headers,
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(written["message"]["instance"], "browser-main");

    let conn = db::connect(&fixture.db_path).unwrap();
    let rows = db::list_messages_after(&conn, 0, Some("human-web"), 10).unwrap();
    assert_eq!(rows.len(), 1);
}

#[tokio::test]
async fn totp_code_is_one_time_per_time_step_and_old_browser_endpoints_are_gone() {
    let fixture = fixture();
    let now = web_auth::current_unix_time();
    let code = web_auth::totp_code_at(&fixture.secret, now, web_auth::TOTP_DIGITS).unwrap();
    let body = json!({"participant_id": fixture.participant_id, "code": code});
    let (first, _) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/totp",
        Some(body.clone()),
        &[],
    )
    .await;
    assert_eq!(first, StatusCode::OK);
    let (replay, _) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/totp",
        Some(body),
        &[],
    )
    .await;
    assert_eq!(replay, StatusCode::UNAUTHORIZED);

    let (old_challenge, _) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/challenge",
        Some(json!({"participant_id": "browser-main"})),
        &[],
    )
    .await;
    assert_eq!(old_challenge, StatusCode::NOT_FOUND);

    let app = include_str!("../web/app.js");
    assert!(!app.contains("bbcred-v1"));
    assert!(!app.contains("crypto.subtle"));
    assert!(!app.contains("private_key"));
    assert!(!app.contains("localStorage"));
    assert!(app.contains("/api/auth/totp"));
    assert!(app.contains("Connecting…"));
    assert!(app.contains("$(\"connect\").addEventListener(\"click\", connect);"));
}

#[tokio::test]
async fn repeated_bad_codes_are_throttled_without_disclosing_participant_state() {
    let fixture = fixture();
    for _ in 0..web_auth::TOTP_MAX_FAILURES {
        let (status, _) = json_request(
            &fixture.router,
            Method::POST,
            "/api/auth/totp",
            Some(json!({"participant_id": fixture.participant_id, "code": "000000"})),
            &[],
        )
        .await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
    }
    let now = web_auth::current_unix_time();
    let valid = web_auth::totp_code_at(&fixture.secret, now, web_auth::TOTP_DIGITS).unwrap();
    let (status, _) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/totp",
        Some(json!({"participant_id": fixture.participant_id, "code": valid})),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}
