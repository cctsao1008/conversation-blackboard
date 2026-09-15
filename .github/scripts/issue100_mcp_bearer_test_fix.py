from pathlib import Path

path = Path("src/mcp_contract_tests.rs")
s = path.read_text(encoding="utf-8")
old = '''    let authorization = audit.authorization.as_ref().unwrap();
    assert_eq!(authorization.principal.provider, "oidc:https://issuer.example");
    assert_eq!(authorization.principal.subject, "remote-agent-1");
    assert_eq!(authorization.mechanism, oidc::OIDC_MECHANISM);
'''
new = '''    let authorization = audit.authorization.as_ref().unwrap();
    let principal = authorization.principal.as_ref().unwrap();
    assert_eq!(principal.provider, "oidc:https://issuer.example");
    assert_eq!(principal.subject, "remote-agent-1");
    assert_eq!(authorization.capability.as_deref(), Some(authorization::POST_MESSAGE));
    assert_eq!(authorization.resource.as_deref(), Some("control-systems"));
    assert_eq!(audit.ingress.len(), 1);
    assert_eq!(audit.ingress[0].principal, *principal);
    assert_eq!(audit.ingress[0].transport, "mcp");
'''
if old not in s:
    raise SystemExit("missing bearer evidence assertion anchor")
path.write_text(s.replace(old, new, 1), encoding="utf-8")
