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
    single_signing_private: String,
    rotary_signing_private: String,
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
    let (single_signing_private, single_public) = signed_auth::generate_keypair();
    let (rotary_signing_private, rotary_public) = signed_auth::generate_keypair();
    identity::set_web_participant_signing_key(&conn, "single-main", &single_public).unwrap();
    identity::set_web_participant_signing_key(&conn, "rotary-main", &rotary_public).unwrap();
    drop(conn);
    let router = mcp::app(AppState {
        db_path: db_path.clone(),
        registration_key: None,
    });
    Fixture {
        _dir: dir,
        db_path,
        router,
        single_signing_private,
        rotary_signing_private,
    }
}

fn signed_write_arguments(
    private_key: &str,
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> Value {
    let signature = signed_auth::sign_write(
        private_key,
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
        "auth": {"scheme": signed_auth::SIGNATURE_SCHEME, "signature": signature}
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
async fn mcp_advertises_signed_only_write_contract() {
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
    let write = &tools[1];
    assert_eq!(write["name"], "blackboard_write");
    assert!(write["inputSchema"]["properties"]["auth"].is_object());
    assert!(write["inputSchema"]["properties"]["private_key"].is_null());
    assert!(write["inputSchema"]["required"]
        .as_array()
        .unwrap()
        .iter()
        .any(|value| value == "auth"));
}

#[tokio::test]
async fn signed_write_is_verified_idempotent_and_readable() {
    let fixture = fixture();
    let arguments = signed_write_arguments(
        &fixture.single_signing_private,
        "single-main",
        "signed-architecture",
        "insight",
        "Private key stays with participant.",
        None,
        "signed-single-001",
    );
    let first = call_tool(&fixture.router, 10, "blackboard_write", arguments.clone()).await;
    assert_eq!(first["result"]["isError"], false);
    let created = &first["result"]["structuredContent"];
    assert_eq!(created["status"], "created");
    assert_eq!(created["source"], "single");
    let id = created["id"].as_i64().unwrap();

    let replay = call_tool(&fixture.router, 11, "blackboard_write", arguments).await;
    assert_eq!(replay["result"]["structuredContent"]["status"], "existing");
    assert_eq!(
        replay["result"]["structuredContent"]["id"].as_i64(),
        Some(id)
    );

    let read = call_tool(
        &fixture.router,
        12,
        "blackboard_read",
        json!({"channel": "signed-architecture", "after": 0, "limit": 50}),
    )
    .await;
    assert_eq!(read["result"]["structuredContent"]["count"], 1);
}

#[tokio::test]
async fn write_rejects_tampering_wrong_identity_missing_auth_and_retired_private_key() {
    let fixture = fixture();
    let original = signed_write_arguments(
        &fixture.single_signing_private,
        "single-main",
        "signed-security",
        "message",
        "original body",
        None,
        "signed-security-001",
    );
    let mut tampered = original.clone();
    tampered["body"] = json!("tampered body");
    let response = call_tool(&fixture.router, 20, "blackboard_write", tampered).await;
    assert_eq!(tool_error_code(&response), "unauthorized");

    let wrong = signed_write_arguments(
        &fixture.single_signing_private,
        "rotary-main",
        "signed-security",
        "message",
        "wrong signer",
        None,
        "signed-security-002",
    );
    let response = call_tool(&fixture.router, 21, "blackboard_write", wrong).await;
    assert_eq!(tool_error_code(&response), "unauthorized");

    let response = call_tool(
        &fixture.router,
        22,
        "blackboard_write",
        json!({
            "participant_id": "single-main",
            "channel": "signed-security",
            "body": "missing auth",
            "nonce": "missing-auth"
        }),
    )
    .await;
    assert_eq!(tool_error_code(&response), "invalid_auth");

    let response = call_tool(
        &fixture.router,
        23,
        "blackboard_write",
        json!({
            "participant_id": "single-main",
            "private_key": "retired",
            "channel": "signed-security",
            "body": "retired raw key",
            "nonce": "retired-key"
        }),
    )
    .await;
    assert_eq!(tool_error_code(&response), "invalid_arguments");
}

#[tokio::test]
async fn signed_write_honors_rotation_revocation_nonce_and_reply_provenance() {
    let fixture = fixture();
    let single = signed_write_arguments(
        &fixture.single_signing_private,
        "single-main",
        "conversation-architecture",
        "idea",
        "Single thought",
        None,
        "single-target",
    );
    let first = call_tool(&fixture.router, 30, "blackboard_write", single).await;
    let single_id = first["result"]["structuredContent"]["id"].as_i64().unwrap();

    let rotary = signed_write_arguments(
        &fixture.rotary_signing_private,
        "rotary-main",
        "conversation-architecture",
        "insight",
        "Rotary reply",
        Some(single_id),
        "rotary-reply",
    );
    let response = call_tool(&fixture.router, 31, "blackboard_write", rotary).await;
    assert_eq!(response["result"]["structuredContent"]["source"], "rotary");
    assert_eq!(
        response["result"]["structuredContent"]["reply_to"].as_i64(),
        Some(single_id)
    );

    let (new_private, new_public) = signed_auth::generate_keypair();
    let conn = db::connect(&fixture.db_path).unwrap();
    identity::set_web_participant_signing_key(&conn, "rotary-main", &new_public).unwrap();
    drop(conn);
    let old = signed_write_arguments(
        &fixture.rotary_signing_private,
        "rotary-main",
        "signed-lifecycle",
        "message",
        "old key",
        None,
        "old-key",
    );
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 32, "blackboard_write", old).await),
        "unauthorized"
    );

    let first = signed_write_arguments(
        &new_private,
        "rotary-main",
        "signed-lifecycle",
        "message",
        "new key",
        None,
        "same-nonce",
    );
    assert_eq!(
        call_tool(&fixture.router, 33, "blackboard_write", first).await["result"]["isError"],
        false
    );
    let conflict = signed_write_arguments(
        &new_private,
        "rotary-main",
        "signed-lifecycle",
        "message",
        "different",
        None,
        "same-nonce",
    );
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 34, "blackboard_write", conflict).await),
        "nonce_conflict"
    );

    let conn = db::connect(&fixture.db_path).unwrap();
    identity::revoke_web_participant_signing_key(&conn, "rotary-main").unwrap();
    drop(conn);
    let revoked = signed_write_arguments(
        &new_private,
        "rotary-main",
        "signed-lifecycle",
        "message",
        "revoked",
        None,
        "revoked",
    );
    assert_eq!(
        tool_error_code(&call_tool(&fixture.router, 35, "blackboard_write", revoked).await),
        "unauthorized"
    );
}

#[tokio::test]
async fn mcp_transport_remains_stateless_and_rejects_unknown_origins() {
    let fixture = fixture();
    let get_response = request(&fixture.router, Method::GET, None).await;
    assert_eq!(get_response.status(), StatusCode::METHOD_NOT_ALLOWED);

    let invalid_origin = fixture
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
                    json!({"jsonrpc": "2.0", "id": 40, "method": "tools/list", "params": {}})
                        .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(invalid_origin.status(), StatusCode::FORBIDDEN);
}
