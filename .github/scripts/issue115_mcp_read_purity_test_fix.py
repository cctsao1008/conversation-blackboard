from pathlib import Path

path = Path('src/mcp_contract_tests.rs')
text = path.read_text()

old_wire = '''        if tool == "blackboard_execution_receipt"\n            || tool == "blackboard_execution_audit"\n            || tool == "blackboard_execution_audit_integrity"\n        {\n            arguments["intent_id"] = json!(resource.unwrap());\n        }\n'''
new_wire = '''        if tool == "blackboard_execution_receipt"\n            || tool == "blackboard_execution_audit"\n            || tool == "blackboard_execution_audit_integrity"\n        {\n            arguments["intent_id"] = json!(resource.unwrap());\n        } else {\n            // capability_arguments() uses intent_id as the wire projection for\n            // resource-scoped execution reads. Sweep and policy-integrity tools\n            // bind the same resource only in their HMAC proof and do not accept\n            // an intent_id argument.\n            arguments.as_object_mut().unwrap().remove("intent_id");\n        }\n'''
if old_wire not in text:
    raise RuntimeError('stale-schema fixture wire anchor not found')
text = text.replace(old_wire, new_wire, 1)

old_verify = '''    let conn = db::connect_read_only(&fixture.db_path).unwrap();\n    let grant_table_count: i64 = conn\n        .query_row(\n            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'principal_grants'",\n            [],\n            |row| row.get(0),\n        )\n        .unwrap();\n    assert_eq!(grant_table_count, 0, "MCP read repaired grant schema");\n    let schema_version_after: i64 = conn\n        .query_row("PRAGMA schema_version", [], |row| row.get(0))\n        .unwrap();\n    drop(conn);\n    assert_eq!(schema_version_after, schema_version_before);\n    assert_eq!(std::fs::read(&fixture.db_path).unwrap(), main_before);\n    for (path, before) in sidecars_before {\n        let after = path.exists().then(|| std::fs::read(&path).unwrap());\n        assert_eq!(after, before, "MCP read mutated SQLite sidecar {}", path.display());\n    }\n'''
new_verify = '''    // Compare durable bytes before reopening SQLite. Even a read-only SQLite\n    // connection may materialize an empty WAL sidecar while inspecting a WAL-mode\n    // database, which would make the test itself look like a mutation.\n    assert_eq!(std::fs::read(&fixture.db_path).unwrap(), main_before);\n    for (path, before) in &sidecars_before {\n        let after = path.exists().then(|| std::fs::read(path).unwrap());\n        assert_eq!(&after, before, "MCP read mutated SQLite sidecar {}", path.display());\n    }\n\n    let conn = db::connect_read_only(&fixture.db_path).unwrap();\n    let grant_table_count: i64 = conn\n        .query_row(\n            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'principal_grants'",\n            [],\n            |row| row.get(0),\n        )\n        .unwrap();\n    assert_eq!(grant_table_count, 0, "MCP read repaired grant schema");\n    let schema_version_after: i64 = conn\n        .query_row("PRAGMA schema_version", [], |row| row.get(0))\n        .unwrap();\n    drop(conn);\n    assert_eq!(schema_version_after, schema_version_before);\n'''
if old_verify not in text:
    raise RuntimeError('stale-schema fixture verification anchor not found')
text = text.replace(old_verify, new_verify, 1)

path.write_text(text)
