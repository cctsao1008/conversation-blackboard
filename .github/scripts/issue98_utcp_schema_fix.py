import json
from pathlib import Path

# Keep the checked-in manual valid for the pinned official UTCP parser.
path = Path("integrations/utcp.json")
data = json.loads(path.read_text(encoding="utf-8"))
removed = data.pop("contract_projection", None)
if removed is None:
    raise SystemExit("contract_projection marker not present")
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

# Projection metadata belongs in project documentation/tests, not as an
# unsupported top-level extension in the official UtcpManual payload.
p = Path("src/contract_parity_tests.rs")
s = p.read_text(encoding="utf-8")
old = '''    let utcp = utcp_json();
    assert_eq!(utcp["contract_projection"]["kind"], "utcp-discovery");
    assert_eq!(
        utcp["contract_projection"]["authority"],
        "rust-application-kernel"
    );
    let tools = utcp["tools"].as_array().unwrap();
'''
new = '''    let utcp = utcp_json();
    assert_eq!(utcp["utcp_version"], "1.1.4");
    assert!(utcp.get("contract_projection").is_none());
    let tools = utcp["tools"].as_array().unwrap();
    assert!(!tools.is_empty());
'''
if old not in s:
    raise SystemExit("missing legacy UTCP projection parity marker")
p.write_text(s.replace(old, new, 1), encoding="utf-8")
