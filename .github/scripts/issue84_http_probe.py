from pathlib import Path

p = Path('src/http_contract_tests.rs')
s = p.read_text(encoding='utf-8')
needle = '''    conn.execute(\n        \"INSERT INTO execution_authorization_provenance\n            (participant_id, intent_id, source, reason, grant_id)\n         VALUES (?1, 'intent-http-audit', 'implicit_authority', 'implicit_human_web', NULL)\",\n        [&fixture.participant_id],\n    )\n    .unwrap();\n    drop(conn);\n\n    let session = web_auth::issue_web_session(&fixture.participant_id);\n'''
replacement = '''    conn.execute(\n        \"INSERT INTO execution_authorization_provenance\n            (participant_id, intent_id, source, reason, grant_id)\n         VALUES (?1, 'intent-http-audit', 'implicit_authority', 'implicit_human_web', NULL)\",\n        [&fixture.participant_id],\n    )\n    .unwrap();\n    assert!(execution::get_execution_audit_bundle(\n        &conn,\n        &fixture.participant_id,\n        \"intent-http-audit\",\n    )\n    .unwrap()\n    .is_some());\n    drop(conn);\n\n    let session = web_auth::issue_web_session(&fixture.participant_id);\n    let receipt_probe = request(\n        &fixture.router,\n        Method::GET,\n        \"/api/executions/intent-http-audit\",\n        Some(&session.token),\n        None,\n    )\n    .await;\n    assert_eq!(receipt_probe.status(), StatusCode::OK);\n'''
if needle not in s:
    raise SystemExit('issue84 HTTP test insertion point not found')
s = s.replace(needle, replacement, 1)
p.write_text(s, encoding='utf-8')
