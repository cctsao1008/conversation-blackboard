use std::{
    io::Write,
    process::{Command, Stdio},
};

use rusqlite::Connection;
use serde_json::Value;
use tempfile::tempdir;

#[test]
fn mcp_stdio_server_runs_real_process_lifecycle_and_tools_call() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");

    // Seed one public channel so the process-level tools/call can exercise a
    // real Blackboard read without introducing credentials into the test.
    let conn = Connection::open(&db_path).unwrap();
    conn.execute_batch(include_str!("../schema.sql")).unwrap();
    conn.execute(
        "INSERT INTO channels (name, visibility, status) VALUES ('blackboard-lounge', 'public', 'active')",
        [],
    )
    .unwrap();
    drop(conn);

    let exe = env!("CARGO_BIN_EXE_conversation-blackboard");
    let mut child = Command::new(exe)
        .args(["mcp", "serve", "--db"])
        .arg(&db_path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();

    {
        let mut stdin = child.stdin.take().unwrap();
        writeln!(
            stdin,
            "{}",
            serde_json::json!({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "stdio-process-test", "version": "1"}
                }
            })
        )
        .unwrap();
        writeln!(
            stdin,
            "{}",
            serde_json::json!({
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            })
        )
        .unwrap();
        writeln!(
            stdin,
            "{}",
            serde_json::json!({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            })
        )
        .unwrap();
        writeln!(
            stdin,
            "{}",
            serde_json::json!({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "blackboard_read",
                    "arguments": {
                        "channel": "blackboard-lounge",
                        "after": 0,
                        "limit": 10
                    }
                }
            })
        )
        .unwrap();
        writeln!(stdin, "{{not-json}}").unwrap();
        // Dropping stdin is the supported clean EOF shutdown path.
    }

    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8(output.stdout).unwrap();
    let responses = stdout
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();

    // initialized is a notification and therefore produces no response.
    assert_eq!(responses.len(), 4);

    assert_eq!(responses[0]["id"], 1);
    assert_eq!(responses[0]["result"]["protocolVersion"], "2025-11-25");
    assert_eq!(
        responses[0]["result"]["serverInfo"]["name"],
        "conversation-blackboard"
    );

    assert_eq!(responses[1]["id"], 2);
    let tools = responses[1]["result"]["tools"].as_array().unwrap();
    assert_eq!(tools.len(), 6);
    assert!(tools.iter().any(|tool| tool["name"] == "blackboard_read"));
    assert!(tools
        .iter()
        .any(|tool| tool["name"] == "blackboard_execution_audit_integrity"));

    assert_eq!(responses[2]["id"], 3);
    assert_eq!(responses[2]["result"]["isError"], false);
    assert_eq!(
        responses[2]["result"]["structuredContent"]["channel"],
        "blackboard-lounge"
    );
    assert_eq!(responses[2]["result"]["structuredContent"]["count"], 0);

    assert!(responses[3]["id"].is_null());
    assert_eq!(responses[3]["error"]["code"], -32700);

    // A protocol/parser failure must not mutate semantic Blackboard state.
    let conn = Connection::open(&db_path).unwrap();
    let message_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM messages", [], |row| row.get(0))
        .unwrap();
    assert_eq!(message_count, 0);
}
