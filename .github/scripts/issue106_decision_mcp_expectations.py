from pathlib import Path

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
replacements = [
    (
        'assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 9);',
        'assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 10);',
        "stdio tools/list count",
    ),
    (
        'assert_eq!(tools.len(), 9);',
        'assert_eq!(tools.len(), 10);',
        "HTTP tools/list count",
    ),
    (
        'assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 9);',
        'assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 10);',
        "modern tools/list count",
    ),
]
for old, new, label in replacements:
    count = s.count(old)
    if count != 1:
        raise SystemExit(f"expected one {label} assertion, found {count}")
    s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
