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

use crate::{db, http::AppState, identity, mcp};

struct Fixture {
    _dir: TempDir,
    db_path: PathBuf,
    router: Router,
    single_key: String,
    rotary_key: String,
}

fn fixture() -> Fixture {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();
    let single_key = "prompt-key-single".to_owned();
    let rotary_key = "prompt-key-rotary".to_owned();
    identity::provision_web_participant(
        &conn,
        "single-main",
        "single",
        Some("Single main conversation"),
        &single_key,
    )
    .unwrap()
    .unwrap();
    identity::provision_web_participant(
        &conn,
        "rotary-main",
        "rotary",
        Some("Rotary main conversation"),
        &rotary_key,
    )
    .unwrap()
    .unwrap();
    drop(conn);

    let router = mcp::app(AppState {
        db_path: db_path.clone(),
        registration_key: None,
    });

    Fixture {
        _dir: dir,
        db_path,
        router,
        single_key,
        rotary_key,
    }
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
    router.clone().oneshot(builder.body(body).unwrap()).await.unwrap()
}

async fn response_json(response: Response) -> (StatusCode, Value) {
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 256 * 1024).await.unwrap();
    let value = serde_json::from_slice(&bytes).unwrap();
    (status, value)
}

async fn call_tool(router: &Router, id: i64, name: &str, arguments: Value) -> Value {
    let response = request(
        router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments
            }
        })),
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    value
}

#[tokio::test]
async fn mcp_initializes_and_advertises_exactly_two_focused_tools() {
    let fixture = fixture();
    let response = request(
        &fixture.router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {
                    "name": "contract-test",
                    "version": "1.0.0"
                }
            }
        })),
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(value["result"]["protocolVersion"], "2025-11-25");
    assert_eq!(value["result"]["capabilities"]["tools"]["listChanged"], false);
    assert_eq!(value["result"]["serverInfo"]["name"], "conversation-blackboard");

    let response = request(
        &fixture.router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        })),
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    let tools = value["result"]["tools"].as_array().unwrap();
    assert_eq!(tools.len(), 2);
    assert_eq!(tools[0]["name"], "blackboard_read");
    assert_eq!(tools[0]["annotations"]["readOnlyHint"], true);
    assert_eq!(tools[0]["annotations"]["openWorldHint"], false);
    assert_eq!(tools[1]["name"], "blackboard_write");
    assert_eq!(tools[1]["annotations"]["readOnlyHint"], false);
    assert_eq!(tools[1]["annotations"]["destructiveHint"], false);
    assert_eq!(tools[1]["annotations"]["idempotentHint"], true);
    assert_eq!(tools[1]["annotations"]["openWorldHint"], false);
}

#[tokio::test]
async fn mcp_write_preserves_server_provenance_and_nonce_idempotency_then_read_returns_it() {
    let fixture = fixture();
    let arguments = json!({
        "participant_id": "single-main",
        "private_key": fixture.single_key,
        "channel": "conversation-architecture",
        "kind": "insight",
        "body": "Shared ideas, separate realities.",
        "nonce": "single-mcp-001"
    });

    let first = call_tool(&fixture.router, 10, "blackboard_write", arguments.clone()).await;
    assert_eq!(first["result"]["isError"], false);
    let created = &first["result"]["structuredContent"];
    assert_eq!(created["status"], "created");
    assert_eq!(created["idempotent"], false);
    assert_eq!(created["source"], "single");
    assert_eq!(created["participant_id"], "single-main");
    assert_eq!(created["instance"], "single-main");
    assert_eq!(created["channel"], "conversation-architecture");
    assert_eq!(created["kind"], "insight");
    let message_id = created["id"].as_i64().unwrap();

    let replay = call_tool(&fixture.router, 11, "blackboard_write", arguments).await;
    let existing = &replay["result"]["structuredContent"];
    assert_eq!(existing["status"], "existing");
    assert_eq!(existing["idempotent"], true);
    assert_eq!(existing["id"].as_i64(), Some(message_id));

    let read = call_tool(
        &fixture.router,
        12,
        "blackboard_read",
        json!({
            "channel": "conversation-architecture",
            "after": 0,
            "limit": 50
        }),
    )
    .await;
    let data = &read["result"]["structuredContent"];
    assert_eq!(data["count"], 1);
    assert_eq!(data["latest_id"].as_i64(), Some(message_id));
    assert_eq!(data["messages"][0]["id"].as_i64(), Some(message_id));
    assert_eq!(data["messages"][0]["source"], "single");
    assert_eq!(data["messages"][0]["instance"], "single-main");
}

#[tokio::test]
async fn mcp_rotary_can_reply_to_authoritative_single_id_and_spoofed_provenance_is_rejected() {
    let fixture = fixture();
    let single = call_tool(
        &fixture.router,
        20,
        "blackboard_write",
        json!({
            "participant_id": "single-main",
            "private_key": fixture.single_key,
            "channel": "conversation-architecture",
            "kind": "idea",
            "body": "Single thought",
            "nonce": "single-reply-target"
        }),
    )
    .await;
    let single_id = single["result"]["structuredContent"]["id"]
        .as_i64()
        .unwrap();

    let rotary = call_tool(
        &fixture.router,
        21,
        "blackboard_write",
        json!({
            "participant_id": "rotary-main",
            "private_key": fixture.rotary_key,
            "channel": "conversation-architecture",
            "kind": "insight",
            "body": "Rotary reply",
            "reply_to": single_id,
            "nonce": "rotary-reply-001"
        }),
    )
    .await;
    let reply = &rotary["result"]["structuredContent"];
    assert_eq!(reply["source"], "rotary");
    assert_eq!(reply["instance"], "rotary-main");
    assert_eq!(reply["reply_to"].as_i64(), Some(single_id));

    let spoof = call_tool(
        &fixture.router,
        22,
        "blackboard_write",
        json!({
            "participant_id": "single-main",
            "private_key": "prompt-key-single",
            "source": "spoofed",
            "instance": "spoofed",
            "channel": "conversation-architecture",
            "body": "must not append",
            "nonce": "spoof-001"
        }),
    )
    .await;
    assert_eq!(spoof["result"]["isError"], true);
    assert_eq!(spoof["result"]["content"][0]["text"], "identity_is_server_resolved");

    let conn = db::connect(&fixture.db_path).unwrap();
    let rows = db::list_messages_after(&conn, 0, Some("conversation-architecture"), 10).unwrap();
    assert_eq!(rows.len(), 2, "spoof attempt must not append a third message");
    assert_eq!(rows[0].source, "single");
    assert_eq!(rows[1].source, "rotary");
    assert_eq!(rows[1].reply_to, Some(single_id));
}

#[tokio::test]
async fn mcp_transport_is_stateless_rejects_unknown_origins_and_accepts_notifications() {
    let fixture = fixture();

    let get_response = request(&fixture.router, Method::GET, None).await;
    assert_eq!(get_response.status(), StatusCode::METHOD_NOT_ALLOWED);
    assert_eq!(
        get_response.headers().get(header::ALLOW).unwrap(),
        "POST, OPTIONS"
    );

    let notification = request(
        &fixture.router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        })),
    )
    .await;
    assert_eq!(notification.status(), StatusCode::ACCEPTED);

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
                    json!({
                        "jsonrpc": "2.0",
                        "id": 30,
                        "method": "tools/list",
                        "params": {}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(invalid_origin.status(), StatusCode::FORBIDDEN);
}
