from pathlib import Path

p = Path("tests/mcp_stdio_server.rs")
s = p.read_text(encoding="utf-8")
old = '    assert_eq!(tools.len(), 8);'
new = '    assert_eq!(tools.len(), 9);'
if old not in s:
    raise SystemExit("missing real-process MCP tool-count anchor")
s = s.replace(old, new, 1)
old = '''    assert!(tools
        .iter()
        .any(|tool| tool["name"] == "blackboard_authorization_policy_integrity"));'''
new = '''    assert!(tools
        .iter()
        .any(|tool| tool["name"] == "blackboard_authorization_policy_integrity"));
    assert!(tools
        .iter()
        .any(|tool| tool["name"] == "blackboard_authorization_policy"));'''
if old not in s:
    raise SystemExit("missing real-process policy tool assertion anchor")
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
