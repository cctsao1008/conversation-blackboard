from pathlib import Path

# Keep the existing MCP contract test aligned with the newly advertised read-only audit tool.
p = Path('src/mcp_contract_tests.rs')
s = p.read_text(encoding='utf-8')
old = 'assert_eq!(tools.len(), 4);'
if old not in s:
    raise SystemExit('expected MCP tool-count assertion not found')
s = s.replace(old, 'assert_eq!(tools.len(), 5);', 1)
p.write_text(s, encoding='utf-8')

# The HTTP audit contract test uses a durable receipt whose message_id must refer to a real
# committed effect. Seed the matching message before inserting the historical receipt.
p = Path('src/http_contract_tests.rs')
s = p.read_text(encoding='utf-8')
needle = '''    execution::ensure_execution_tables(&conn).unwrap();\n    authorization::ensure_grant_schema(&conn).unwrap();\n    conn.execute(\n        \"INSERT INTO execution_receipts\n'''
replacement = '''    execution::ensure_execution_tables(&conn).unwrap();\n    authorization::ensure_grant_schema(&conn).unwrap();\n    let seed = db::append_message(\n        &conn,\n        &fixture.identity,\n        \"blackboard-lounge\",\n        \"message\",\n        \"audit seed\",\n        None,\n    )\n    .unwrap();\n    assert_eq!(seed.id, 1);\n    conn.execute(\n        \"INSERT INTO execution_receipts\n'''
if needle not in s:
    raise SystemExit('expected HTTP audit fixture insertion point not found')
s = s.replace(needle, replacement, 1)
p.write_text(s, encoding='utf-8')
