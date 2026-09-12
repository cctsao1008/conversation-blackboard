use std::path::PathBuf;

use axum::{
    body::{to_bytes, Body},
    http::{header, Method, Request, StatusCode},
    response::Response,
    Router,
};
use serde_json::{json, Value};
use tempfile::{tempdir, TempDir};
use tower::ServiceExt;

use crate::{db, http::AppState, identity, mcp, signed_auth};

struct Fixture {
    _dir: TempDir,
    db_path: PathBuf,
    router: Router,
    single_secret: String,
    rotary_secret: String,
}

fn fixture() -> Fixture {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();
    identity::provision_web_participant_identity(&conn, "single-main", "single", Some("Single"))
        .unwrap()
        .unwrap();
    identity::provision_web_participant_identity(&conn, "rotary-main", "rotary", Some("Rotary"))
        .unwrap()
        .unwrap();
    let (single_secret, _) = signed_auth::generate_secret();
    let (rotary_secret, _) = signed_auth::generate_secret();
    identity::set_web_participant_auth_secret(&conn, "single-main", &single_secret).unwrap();
    identity::set_web_participant_auth_secret(&conn, "rotary-main", &rotary_secret).unwrap();
    drop(conn);
    let router = mcp::app(AppState {
        db_path: db_path.clone(),
        registration_key: None,
    });
    Fixture {
        _dir: dir,
        db_path,
        router,
        single_secret,
        rotary_secret,
    }
}

fn write_arguments(
    secret: &str,
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> Value {
    let proof = signed_auth::compute_write_proof(
        secret,
        participant_id,
        channel,
        kind,
        body,
        reply_to,
        nonce,
    )
    .unwrap();
    json!({
        "participant_id": participant_id,
        "channel": channel,
        "kind": kind,
        "body": body,
        "reply_to": reply_to,
        "nonce": nonce,
        "auth": {"scheme": signed_auth::SIGNATURE_SCHEME, "proof": proof}
    })
}

fn read_arguments(
    secret: &str,
    participant_id: &str,
    channel: &str,
    after: i64,
    limit: usize,
) -> Value {
    let proof =
        signed_auth::compute_read_proof(secret, participant_id, channel, after, limit).unwrap();
    json!({
        "participant_id": participant_id,
        "channel": channel,
        "after": after,
        "limit": limit,
        "auth": {"scheme": signed_auth::SIGNATURE_SCHEME, "proof": proof}
    })
}

async fn request(router: &Router, method: Method, body: Option<Value>) -> Response {
    let mut builder = Request::builder().method(method).uri("/mcp");
    if body.is_some() {
        builder = builder
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::ACCEPT, "application/json, text/event-stream")
            .header("mcp-protocol-version", "2025-11-25");
    }
    let body = body
        .map(|value| Body::from(value.to_string()))
        .unwrap_or_else(Body::empty);
    router
        .clone()
        .oneshot(builder.body(body).unwrap())
        .await
        .unwrap()
}

async fn response_json(response: Response) -> (StatusCode, Value) {
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 256 * 1024).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

async fn call_tool(router: &Router, id: i64, name: &str, arguments: Value) -> Value {
    let response = request(
        router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments}
        })),
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    value
}

fn tool_error_code(value: &Value) -> &str {
    value["result"]["content"][0]["text"].as_str().unwrap()
}

#[tokio::test]
async fn mcp_advertises_hmac_only_auth_contract() {
    let fixture = fixture();
    let response = request(
        &fixture.router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {}
        })),
    )
    .await;
    let (_, value) = response_json(response).await;
    let tools = value["result"]["tools"].as_array().unwrap();
    assert_eq!(tools.len(), 2);
    for tool in tools {
        let auth = &tool["inputSchema"]["properties"]["auth"];
        assert_eq!(
            auth["properties"]["scheme"]["enum"][0],
            signed_auth::SIGNATURE_SCHEME
        );
        assert!(auth["properties"]["proof"].is_object());
        assert!(auth["properties"]["signature"].is_null());
        assert!(auth["properties"]["private_key"].is_null());
    }
}

#[tokio::test]
async fn public_read_is_unsigned_private_read_requires_valid_hmac() {
    let fixture = fixture();
    let public_write = write_arguments(
        &fixture.single_secret,
        "single-main",
        "blackboard-lounge",
        "banter",
        "public",
        None,
        "public-001",
    );
    assert!(
        !call_tool(&fixture.router, 2, "blackboard_write", public_write).await["result"]["isError"]
            .as_bool()
            .unwrap()
    );

    let public_read = call_tool(
        &fixture.router,
        3,
        "blackboard_read",
        json!({"channel": "blackboard-lounge", "after": 0, "limit": 50}),
    )
    .await;
    assert_eq!(public_read["result"]["structuredContent"]["count"], 1);

    let private_write = write_arguments(
        &fixture.single_secret,
        "single-main",
        "control-systems",
        "insight",
        "private",
        None,
        "private-001",
    );
    assert!(
        !call_tool(&fixture.router, 4, "blackboard_write", private_write).await["result"]
            ["isError"]
            .as_bool()
            .unwrap()
    );

    let unsigned = call_tool(
        &fixture.router,
        5,
        "blackboard_read",
        json!({"channel": "control-systems", "after": 0, "limit": 50}),
    )
    .await;
    assert_eq!(tool_error_code(&unsigned), "forbidden");

    let authenticated = call_tool(
        &fixture.router,
        6,
        "blackboard_read",
        read_arguments(
            &fixture.single_secret,
            "single-main",
            "control-systems",
            0,
            50,
        ),
    )
    .await;
    assert_eq!(authenticated["result"]["structuredContent"]["count"], 1);

    let mut tampered = read_arguments(
        &fixture.single_secret,
        "single-main",
        "control-systems",
        0,
        50,
    );
    tampered["limit"] = json!(49);
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 7, "blackboard_read", tampered).await),
        "unauthorized"
    );
}

#[tokio::test]
async fn hmac_write_rejects_wrong_secret_modified_payload_and_wrong_identity() {
    let fixture = fixture();
    let original = write_arguments(
        &fixture.single_secret,
        "single-main",
        "hmac-security",
        "message",
        "original body",
        None,
        "hmac-security-001",
    );

    let mut modified = original.clone();
    modified["body"] = json!("tampered body");
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 10, "blackboard_write", modified).await),
        "unauthorized"
    );

    let wrong_secret = write_arguments(
        &fixture.rotary_secret,
        "single-main",
        "hmac-security",
        "message",
        "wrong secret",
        None,
        "hmac-security-002",
    );
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 11, "blackboard_write", wrong_secret).await),
        "unauthorized"
    );

    let wrong_identity = write_arguments(
        &fixture.single_secret,
        "rotary-main",
        "hmac-security",
        "message",
        "wrong identity",
        None,
        "hmac-security-003",
    );
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 12, "blackboard_write", wrong_identity).await),
        "unauthorized"
    );
}

#[tokio::test]
async fn hmac_write_is_idempotent_and_nonce_conflict_is_preserved() {
    let fixture = fixture();
    let first = write_arguments(
        &fixture.single_secret,
        "single-main",
        "hmac-idempotency",
        "message",
        "first",
        None,
        "same-nonce",
    );
    let created = call_tool(&fixture.router, 20, "blackboard_write", first.clone()).await;
    assert_eq!(created["result"]["structuredContent"]["status"], "created");
    let id = created["result"]["structuredContent"]["id"]
        .as_i64()
        .unwrap();

    let replay = call_tool(&fixture.router, 21, "blackboard_write", first).await;
    assert_eq!(replay["result"]["structuredContent"]["status"], "existing");
    assert_eq!(replay["result"]["structuredContent"]["id"], id);

    let conflict = write_arguments(
        &fixture.single_secret,
        "single-main",
        "hmac-idempotency",
        "message",
        "different",
        None,
        "same-nonce",
    );
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 22, "blackboard_write", conflict).await),
        "nonce_conflict"
    );
}

#[tokio::test]
async fn inactive_revoke_and_rotate_change_hmac_authority_immediately() {
    let fixture = fixture();
    let old_secret = fixture.rotary_secret.clone();

    let conn = db::connect(&fixture.db_path).unwrap();
    identity::set_web_participant_status(&conn, "rotary-main", "inactive").unwrap();
    drop(conn);
    let inactive = write_arguments(
        &old_secret,
        "rotary-main",
        "hmac-lifecycle",
        "message",
        "inactive",
        None,
        "inactive-001",
    );
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 30, "blackboard_write", inactive).await),
        "unauthorized"
    );

    let conn = db::connect(&fixture.db_path).unwrap();
    identity::set_web_participant_status(&conn, "rotary-main", "active").unwrap();
    let (new_secret, _) = signed_auth::generate_secret();
    identity::set_web_participant_auth_secret(&conn, "rotary-main", &new_secret).unwrap();
    drop(conn);

    let old = write_arguments(
        &old_secret,
        "rotary-main",
        "hmac-lifecycle",
        "message",
        "old",
        None,
        "old-001",
    );
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 31, "blackboard_write", old).await),
        "unauthorized"
    );

    let fresh = write_arguments(
        &new_secret,
        "rotary-main",
        "hmac-lifecycle",
        "message",
        "new",
        None,
        "new-001",
    );
    assert!(
        !call_tool(&fixture.router, 32, "blackboard_write", fresh).await["result"]["isError"]
            .as_bool()
            .unwrap()
    );

    let conn = db::connect(&fixture.db_path).unwrap();
    identity::revoke_web_participant_auth(&conn, "rotary-main").unwrap();
    drop(conn);
    let revoked = write_arguments(
        &new_secret,
        "rotary-main",
        "hmac-lifecycle",
        "message",
        "revoked",
        None,
        "revoked-001",
    );
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 33, "blackboard_write", revoked).await),
        "unauthorized"
    );
}

#[tokio::test]
async fn mcp_transport_remains_stateless_and_rejects_unknown_origins() {
    let fixture = fixture();
    assert_eq!(
        request(&fixture.router, Method::GET, None).await.status(),
        StatusCode::METHOD_NOT_ALLOWED
    );

    let response = fixture
        .router
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/mcp")
                .header(header::CONTENT_TYPE, "application/json")
                .header(header::ACCEPT, "application/json, text/event-stream")
                .header(header::ORIGIN, "https://evil.example")
                .body(Body::from(
                    json!({"jsonrpc":"2.0","id":1,"method":"ping"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}
