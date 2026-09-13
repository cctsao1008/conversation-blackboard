from pathlib import Path

p = Path('src/http_contract_tests.rs')
s = p.read_text(encoding='utf-8')
start = s.find('async fn execution_audit_http_is_policy_guarded_and_reads_committed_evidence()')
if start < 0:
    raise SystemExit('issue84 HTTP test not found')
marker = '    drop(conn);\n\n    let session = web_auth::issue_web_session(&fixture.participant_id);\n'
pos = s.find(marker, start)
if pos < 0:
    raise SystemExit('issue84 HTTP drop/session boundary not found')
probe = '''    assert!(execution::get_execution_audit_bundle(\n        &conn,\n        &fixture.participant_id,\n        \"intent-http-audit\",\n    )\n    .unwrap()\n    .is_some());\n    drop(conn);\n\n    let session = web_auth::issue_web_session(&fixture.participant_id);\n    let receipt_probe = request(\n        &fixture.router,\n        Method::GET,\n        \"/api/executions/intent-http-audit\",\n        Some(&session.token),\n        None,\n    )\n    .await;\n    assert_eq!(receipt_probe.status(), StatusCode::OK);\n'''
s = s[:pos] + probe + s[pos + len(marker):]
p.write_text(s, encoding='utf-8')
