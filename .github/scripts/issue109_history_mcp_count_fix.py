from pathlib import Path

path = Path("src/mcp_contract_tests.rs")
text = path.read_text()
old = '    assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 10);'
new = '    assert_eq!(value["result"]["tools"].as_array().unwrap().len(), 11);'
count = text.count(old)
if count != 1:
    raise RuntimeError(f"modern tools/list inventory anchor: expected 1, found {count}")
path.write_text(text.replace(old, new, 1))
