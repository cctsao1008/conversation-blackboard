from pathlib import Path

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")

old = '''    // Snapshot authority alone does not imply policy-integrity authority.\n    let integrity = call_tool(\n        &fixture.router,\n        126,\n        "blackboard_authorization_policy_integrity",\n        policy_integrity_arguments(&fixture.single_secret, "single-main"),\n    )\n    .await;\n    // An explicit integrity grant was deliberately inserted above, so remove it\n    // before asserting separation in the reverse direction.\n    assert_eq!(integrity["result"]["isError"], false);\n    let conn = db::connect(&fixture.db_path).unwrap();\n    conn.execute(\n        "UPDATE principal_grants SET status = 'inactive'\n         WHERE principal_provider = 'participant-hmac'\n           AND principal_subject = 'single-main'\n           AND participant_id = 'single-main'\n           AND capability = 'read_authorization_policy_integrity'",\n        [],\n    )\n    .unwrap();\n    conn.execute("DROP TABLE delegated_grants", []).unwrap();\n    drop(conn);'''
new = '''    // Snapshot authority alone does not imply policy-integrity authority.\n    let conn = db::connect(&fixture.db_path).unwrap();\n    conn.execute(\n        "UPDATE principal_grants SET status = 'inactive'\n         WHERE principal_provider = 'participant-hmac'\n           AND principal_subject = 'single-main'\n           AND participant_id = 'single-main'\n           AND capability = 'read_authorization_policy_integrity'",\n        [],\n    )\n    .unwrap();\n    drop(conn);\n    let integrity = call_tool(\n        &fixture.router,\n        126,\n        "blackboard_authorization_policy_integrity",\n        policy_integrity_arguments(&fixture.single_secret, "single-main"),\n    )\n    .await;\n    assert_eq!(tool_error_code(&integrity), "forbidden");\n\n    let conn = db::connect(&fixture.db_path).unwrap();\n    conn.execute("DROP TABLE delegated_grants", []).unwrap();\n    drop(conn);'''
if old not in s:
    raise SystemExit("missing reverse-separation anchor")
s = s.replace(old, new, 1)

old = '''    assert!(allowed_value["result"]["structuredContent"]["policy"]["durable_grants"]\n        .as_array()\n        .unwrap()\n        .iter()\n        .any(|entry| entry["principal_subject"] == "policy-reader"));\n}\n\n#[tokio::test]\nasync fn mcp_policy_integrity_requires_its_own_explicit_hmac_grant_and_never_migrates_schema() {'''
new = '''    assert!(allowed_value["result"]["structuredContent"]["policy"]["durable_grants"]\n        .as_array()\n        .unwrap()\n        .iter()\n        .any(|entry| entry["principal_subject"] == "policy-reader"));\n\n    // A verified Bearer must not make a stale authorization schema writable.\n    let conn = db::connect(&fixture.db_path).unwrap();\n    conn.execute("DROP TABLE delegated_grants", []).unwrap();\n    drop(conn);\n    let legacy = request_with_authorization(\n        &router,\n        json!({\n            "jsonrpc": "2.0",\n            "id": 130,\n            "method": "tools/call",\n            "params": {\n                "name": "blackboard_authorization_policy",\n                "arguments": {"participant_id": "single-main"}\n            }\n        }),\n        &authorization_header,\n    )\n    .await;\n    let (status, legacy_value) = response_json(legacy).await;\n    assert_eq!(status, StatusCode::OK);\n    assert_eq!(tool_error_code(&legacy_value), "authorization_schema_not_current");\n    let conn = db::connect(&fixture.db_path).unwrap();\n    let table_count: i64 = conn\n        .query_row(\n            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'delegated_grants'",\n            [],\n            |row| row.get(0),\n        )\n        .unwrap();\n    assert_eq!(table_count, 0, "Bearer snapshot read recreated authorization schema");\n}\n\n#[tokio::test]\nasync fn mcp_policy_integrity_requires_its_own_explicit_hmac_grant_and_never_migrates_schema() {'''
if old not in s:
    raise SystemExit("missing bearer legacy-schema anchor")
s = s.replace(old, new, 1)

old = '''    assert!(tool_required_fields(&value, "blackboard_write")\n        .iter()\n        .any(|field| field == "auth"));\n}\n\n#[tokio::test]\nasync fn modern_tools_list_uses_per_request_metadata_and_cacheable_complete_result() {'''
new = '''    for name in ["blackboard_write", "blackboard_authorization_policy"] {\n        assert!(tool_required_fields(&value, name)\n            .iter()\n            .any(|field| field == "auth"));\n    }\n}\n\n#[tokio::test]\nasync fn modern_tools_list_uses_per_request_metadata_and_cacheable_complete_result() {'''
if old not in s:
    raise SystemExit("missing HMAC-only modern projection anchor")
s = s.replace(old, new, 1)

# The delegated canonical schema deliberately excludes CLI/private timestamps.
old = '''    assert_eq!(tool["annotations"]["readOnlyHint"], true);\n    assert!(tool["inputSchema"]["required"]'''
new = '''    assert_eq!(tool["annotations"]["readOnlyHint"], true);\n    let delegated_properties = &tool["outputSchema"]["properties"]["policy"]\n        ["properties"]["delegated_grants"]["items"]["properties"];\n    assert!(delegated_properties.get("created_at").is_none());\n    assert!(delegated_properties.get("updated_at").is_none());\n    assert!(tool["inputSchema"]["required"]'''
marker = 'async fn mcp_policy_snapshot_tool_uses_canonical_read_only_schema()'
pos = s.find(marker)
if pos < 0:
    raise SystemExit("missing snapshot schema test")
end = s.find('#[tokio::test]', pos + len(marker))
region = s[pos:end]
if old not in region:
    raise SystemExit("missing snapshot schema assertion anchor")
region = region.replace(old, new, 1)
s = s[:pos] + region + s[end:]

old = '    assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 8);'
new = '    assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 9);'
if old not in s:
    raise SystemExit("missing stdio tool-count anchor")
s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
