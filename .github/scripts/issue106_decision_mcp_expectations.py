from pathlib import Path

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
old = 'assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 9);'
if s.count(old) != 1:
    raise SystemExit(f"expected one stdio tool-count assertion, found {s.count(old)}")
s = s.replace(old, 'assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 10);', 1)
old = 'assert_eq!(tools.len(), 9);'
if s.count(old) != 2:
    raise SystemExit(f"expected two tool-count assertions, found {s.count(old)}")
s = s.replace(old, 'assert_eq!(tools.len(), 10);')
p.write_text(s, encoding="utf-8")
