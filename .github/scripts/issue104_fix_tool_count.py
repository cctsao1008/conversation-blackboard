from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")

s = replace_once(
    s,
    '''    let listed = mcp::stdio_dispatch(\n        &state,\n        &json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),\n    )\n    .await\n    .unwrap();\n    assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 7);''',
    '''    let listed = mcp::stdio_dispatch(\n        &state,\n        &json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),\n    )\n    .await\n    .unwrap();\n    assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 8);''',
    "stdio tool count",
)

s = replace_once(
    s,
    '''    let tools = value["result"]["tools"].as_array().unwrap();\n    assert_eq!(tools.len(), 7);''',
    '''    let tools = value["result"]["tools"].as_array().unwrap();\n    assert_eq!(tools.len(), 8);''',
    "legacy HTTP tool count",
)

s = replace_once(
    s,
    '''    assert_eq!(value["result"]["resultType"], "complete");\n    assert_eq!(value["result"]["cacheScope"], "public");\n    assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 7);''',
    '''    assert_eq!(value["result"]["resultType"], "complete");\n    assert_eq!(value["result"]["cacheScope"], "public");\n    assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 8);''',
    "modern HTTP tool count",
)

p.write_text(s, encoding="utf-8")
