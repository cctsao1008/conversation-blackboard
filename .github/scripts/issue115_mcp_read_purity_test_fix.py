from pathlib import Path

path = Path('src/mcp_contract_tests.rs')
text = path.read_text()
old = '''        if tool == "blackboard_execution_receipt"\n            || tool == "blackboard_execution_audit"\n            || tool == "blackboard_execution_audit_integrity"\n        {\n            arguments["intent_id"] = json!(resource.unwrap());\n        }\n'''
new = '''        if tool == "blackboard_execution_receipt"\n            || tool == "blackboard_execution_audit"\n            || tool == "blackboard_execution_audit_integrity"\n        {\n            arguments["intent_id"] = json!(resource.unwrap());\n        } else {\n            // capability_arguments() uses intent_id as the wire projection for\n            // resource-scoped execution reads. Sweep and policy-integrity tools\n            // bind the same resource only in their HMAC proof and do not accept\n            // an intent_id argument.\n            arguments.as_object_mut().unwrap().remove("intent_id");\n        }\n'''
if old not in text:
    raise RuntimeError('stale-schema fixture anchor not found')
path.write_text(text.replace(old, new, 1))
