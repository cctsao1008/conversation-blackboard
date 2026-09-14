from pathlib import Path

p = Path("tests/mcp_stdio_server.rs")
s = p.read_text(encoding="utf-8")
old = '''    assert_eq!(tools.len(), 6);
    assert!(tools.iter().any(|tool| tool["name"] == "blackboard_read"));
    assert!(tools
        .iter()
        .any(|tool| tool["name"] == "blackboard_execution_audit_integrity"));
'''
new = '''    assert_eq!(tools.len(), 7);
    assert!(tools.iter().any(|tool| tool["name"] == "blackboard_read"));
    assert!(tools
        .iter()
        .any(|tool| tool["name"] == "blackboard_execution_audit_integrity"));
    assert!(tools
        .iter()
        .any(|tool| tool["name"] == "blackboard_execution_audit_sweep"));
'''
if old not in s:
    raise SystemExit("missing MCP stdio tool-count marker")
p.write_text(s.replace(old, new, 1), encoding="utf-8")
