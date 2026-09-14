import json
from pathlib import Path

utcp_path = Path("integrations/utcp.json")
data = json.loads(utcp_path.read_text(encoding="utf-8"))
if any(tool.get("name") == "execution_audit_sweep" for tool in data["tools"]):
    raise SystemExit("UTCP audit sweep already present")

data["manual_version"] = "1.0.2"
data["tools"].append(
    {
        "name": "execution_audit_sweep",
        "description": "Run the privileged database-wide execution audit integrity sweep. Authorization is enforced by the Blackboard kernel; invalid integrity state is returned as report data and no repair is performed.",
        "inputs": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "outputs": {
            "type": "object",
            "required": ["sweep"],
            "properties": {
                "sweep": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "valid",
                        "executions_scanned",
                        "invalid_executions",
                        "orphan_evidence",
                    ],
                    "properties": {
                        "valid": {"type": "boolean"},
                        "executions_scanned": {"type": "integer", "minimum": 0},
                        "invalid_executions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "participant_id",
                                    "intent_id",
                                    "valid",
                                    "checks",
                                    "violations",
                                ],
                                "properties": {
                                    "participant_id": {"type": "string"},
                                    "intent_id": {"type": "string"},
                                    "valid": {"type": "boolean"},
                                    "checks": {"type": "array", "items": {"type": "string"}},
                                    "violations": {"type": "array", "items": {"type": "string"}},
                                },
                            },
                        },
                        "orphan_evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["kind", "participant_id", "intent_id", "reference"],
                                "properties": {
                                    "kind": {"type": "string"},
                                    "participant_id": {"type": ["string", "null"]},
                                    "intent_id": {"type": "string"},
                                    "reference": {"type": ["string", "null"]},
                                },
                            },
                        },
                    },
                }
            },
        },
        "tags": ["blackboard", "execution", "audit", "integrity", "sweep", "privileged", "read"],
        "tool_call_template": {
            "name": "blackboard_execution_audit_sweep_http",
            "call_template_type": "http",
            "url": "${BLACKBOARD_URL}/api/execution-audit/sweep",
            "http_method": "GET",
            "content_type": "application/json",
            "body_field": None,
            "auth": {
                "auth_type": "api_key",
                "api_key": "Bearer ${BLACKBOARD_TOKEN}",
                "var_name": "Authorization",
                "location": "header",
            },
        },
    }
)
utcp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

p = Path("src/contract_parity_tests.rs")
s = p.read_text(encoding="utf-8")
old = '''    let utcp = utcp_json();
    assert!(utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .any(|tool| tool["name"] == "execution_audit_integrity"));
}'''
new = '''    let utcp = utcp_json();
    assert!(utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .any(|tool| tool["name"] == "execution_audit_integrity"));
}'''
if old not in s:
    raise SystemExit("missing existing UTCP integrity parity marker")

sweep_test_marker = '''fn execution_audit_sweep_contracts_match_canonical_rust_shape() {'''
if sweep_test_marker not in s:
    raise SystemExit("missing sweep parity test")
old_tail = '''    assert!(api["paths"].get("/api/execution-audit/sweep").is_some());
}
'''
new_tail = '''    assert!(api["paths"].get("/api/execution-audit/sweep").is_some());

    let utcp = utcp_json();
    let sweep = utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "execution_audit_sweep")
        .expect("UTCP sweep projection must exist");
    assert_eq!(
        names(sweep, "/outputs/properties/sweep/properties"),
        names(&contract_schema::execution_audit_sweep_schema(), "/properties")
    );
    assert_eq!(
        names(
            sweep,
            "/outputs/properties/sweep/properties/orphan_evidence/items/properties"
        ),
        names(
            &contract_schema::execution_audit_orphan_evidence_schema(),
            "/properties"
        )
    );
    assert_eq!(
        sweep["tool_call_template"]["url"],
        "${BLACKBOARD_URL}/api/execution-audit/sweep"
    );
}
'''
if old_tail not in s:
    raise SystemExit("missing sweep parity tail")
p.write_text(s.replace(old_tail, new_tail, 1), encoding="utf-8")
