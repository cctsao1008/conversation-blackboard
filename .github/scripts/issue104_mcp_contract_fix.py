from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")

s = replace_once(
    s,
    '''    let listed = mcp::stdio_dispatch(
        &state,
        &json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    )
    .await
    .unwrap();
    assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 7);''',
    '''    let listed = mcp::stdio_dispatch(
        &state,
        &json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    )
    .await
    .unwrap();
    assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 8);''',
    "stdio tool count",
)

s = replace_once(
    s,
    '''    let (_, value) = response_json(response).await;
    let tools = value["result"]["tools"].as_array().unwrap();
    assert_eq!(tools.len(), 7);
    for tool in tools {''',
    '''    let (_, value) = response_json(response).await;
    let tools = value["result"]["tools"].as_array().unwrap();
    assert_eq!(tools.len(), 8);
    for tool in tools {''',
    "legacy HTTP tool count",
)

anchor = '''#[tokio::test]
async fn mcp_read_schema_includes_conversation_ref_provenance() {'''
new_test = r'''#[tokio::test]
async fn mcp_policy_integrity_tool_uses_canonical_read_only_schema() {
    let fixture = fixture();
    let response = request(
        &fixture.router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/list",
            "params": {}
        })),
    )
    .await;
    let (_, value) = response_json(response).await;
    let tool = value["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "blackboard_authorization_policy_integrity")
        .expect("policy integrity tool must be advertised");
    assert_eq!(
        tool["outputSchema"],
        contract_schema::authorization_integrity_envelope_schema()
    );
    assert_eq!(tool["annotations"]["readOnlyHint"], true);
    assert!(tool["inputSchema"]["required"]
        .as_array()
        .unwrap()
        .iter()
        .any(|field| field == "auth"));
}

''' + anchor
s = replace_once(s, anchor, new_test, "policy integrity MCP schema regression")

s = replace_once(
    s,
    '''        "blackboard_execution_audit_integrity",
        "blackboard_execution_audit_sweep",
    ] {''',
    '''        "blackboard_execution_audit_integrity",
        "blackboard_execution_audit_sweep",
        "blackboard_authorization_policy_integrity",
    ] {''',
    "modern bearer projection tool set",
)

s = replace_once(
    s,
    '''    assert_eq!(value["result"]["cacheScope"], "public");
    assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 7);''',
    '''    assert_eq!(value["result"]["cacheScope"], "public");
    assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 8);''',
    "modern HTTP tool count",
)

p.write_text(s, encoding="utf-8")
