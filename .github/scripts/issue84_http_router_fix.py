from pathlib import Path

p = Path('src/http_contract_tests.rs')
s = p.read_text(encoding='utf-8')
name = 'async fn execution_audit_http_is_policy_guarded_and_reads_committed_evidence()'
start = s.find(name)
if start < 0:
    raise SystemExit('issue84 HTTP audit test not found')
next_test = s.find('\n#[tokio::test]', start + len(name))
end = len(s) if next_test < 0 else next_test
segment = s[start:end]
needle = '    let session = web_auth::issue_web_session(&fixture.participant_id);\n'
if needle not in segment:
    raise SystemExit('issue84 session boundary not found')
router = '''    let audit_router = fixture.router.clone().merge(crate::access_api::app(AppState {\n        db_path: fixture.db_path.clone(),\n        registration_key: None,\n    }));\n    let session = web_auth::issue_web_session(&fixture.participant_id);\n'''
segment = segment.replace(needle, router, 1)
segment = segment.replace('&fixture.router,', '&audit_router,')
s = s[:start] + segment + s[end:]
p.write_text(s, encoding='utf-8')
