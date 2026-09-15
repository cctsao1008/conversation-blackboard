from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# Canonical authorization-kernel separation regression.
p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")
marker = '''    #[test]\n    fn authorization_integrity_accepts_clean_expiry_and_inactive_history() {'''
insert = r'''    #[test]
    fn authorization_policy_snapshot_authority_does_not_imply_other_privileged_capabilities() {
        let (_dir, conn) = setup();
        let oidc = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "policy-reader".to_owned(),
        };
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                oidc.provider,
                oidc.subject,
                READ_AUTHORIZATION_POLICY,
                AUTHORIZATION_POLICY_RESOURCE
            ],
        )
        .unwrap();

        assert!(authorize(
            &conn,
            &oidc,
            "maker-main",
            READ_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_RESOURCE),
        )
        .unwrap());

        for (capability, resource) in [
            (
                READ_AUTHORIZATION_POLICY_INTEGRITY,
                Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            ),
            (READ_EXECUTION_AUDIT, None),
            (
                READ_EXECUTION_AUDIT_SWEEP,
                Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            ),
            (MANAGE_CHANNELS, None),
        ] {
            assert!(
                !authorize(&conn, &oidc, "maker-main", capability, resource).unwrap(),
                "snapshot authority leaked into {capability}"
            );
        }
    }

'''
if "authorization_policy_snapshot_authority_does_not_imply_other_privileged_capabilities" in s:
    raise SystemExit("authority separation regression already exists")
s = replace_once(s, marker, insert + marker, "authorization separation test")
p.write_text(s, encoding="utf-8")

# Adapter architectural guard: projection layers reuse the canonical reader and
# never acquire a second SQL implementation for policy inventory.
p = Path("src/contract_parity_tests.rs")
s = p.read_text(encoding="utf-8")
if "authorization_policy_snapshot_adapters_reuse_canonical_reader_without_grant_sql" in s:
    raise SystemExit("adapter SQL guard already exists")
s += r'''

#[test]
fn authorization_policy_snapshot_adapters_reuse_canonical_reader_without_grant_sql() {
    let adapters = [
        ("http", include_str!("main.rs")),
        ("mcp", include_str!("mcp.rs")),
    ];
    for (name, source) in adapters {
        assert!(
            source.contains("read_authorization_policy_snapshot"),
            "{name} adapter must reuse the canonical authorization snapshot reader"
        );
        for forbidden in [
            "FROM principal_grants",
            "FROM delegated_grants",
            "JOIN principal_grants",
            "JOIN delegated_grants",
        ] {
            assert!(
                !source.contains(forbidden),
                "{name} adapter must not enumerate grant tables directly: {forbidden}"
            );
        }
    }
}
'''
p.write_text(s, encoding="utf-8")

# Durable architecture documentation.
p = Path("docs/authorization-policy.md")
s = p.read_text(encoding="utf-8")
section = r'''

## Policy observation boundaries

Authorization policy now exposes four deliberately separate concerns:

```text
authorization policy snapshot
    = which explicit durable and delegated authority objects exist

authorization policy integrity
    = whether those stored authority objects are structurally coherent

authorization decision / explain
    = why one requested action is allowed or denied

authorization administration
    = creation, reactivation, deactivation, or other mutation of authority
```

The policy snapshot is a privileged observational surface, not a grant-administration surface. The canonical reader is `authorization::read_authorization_policy_snapshot(conn)`. REST `GET /api/authorization-policy`, MCP `blackboard_authorization_policy`, OpenAPI, and UTCP project that same domain result instead of enumerating grant tables in adapters.

Snapshot visibility has its own capability and global resource:

```text
capability = read_authorization_policy
resource   = authorization-policy
```

Implicit authority is intentionally narrow: only a `human-web` principal authenticating as its own admin participant receives implicit snapshot visibility. Participant HMAC self-authentication, GitHub owner compatibility authority, and OIDC/Bearer identity do not imply this capability. They require an explicit matching Blackboard grant. Conversely, a snapshot grant does not imply policy-integrity, execution-audit, audit-sweep, or channel-administration authority.

The reader is observationally read-only. It does not call `ensure_grant_schema`, initialize storage, repair policy state, consume delegated grants, or normalize lifecycle state. An authorization schema too old for the snapshot contract is reported as migration-required without database mutation. Inactive durable grants plus expired or consumed delegated grants remain visible as inventory state rather than being silently filtered or reclassified as integrity failures.

The snapshot contract contains only non-secret authority metadata. Bearer/JWT values, HMAC secrets, TOTP material, session credentials, and participant authentication secrets are outside the snapshot model.
'''
if "## Policy observation boundaries" not in s:
    anchor = "\n## Verification boundary\n"
    if anchor not in s:
        raise SystemExit("authorization-policy verification anchor missing")
    s = s.replace(anchor, section + anchor, 1)
p.write_text(s, encoding="utf-8")

p = Path("docs/contract-projections.md")
s = p.read_text(encoding="utf-8")
old = "`src/contract_schema.rs` owns reusable externally visible semantic shapes used directly by MCP and by parity tests for the HTTP/UTCP projections. It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, execution receipt, execution-audit integrity, and authorization-policy integrity report/violation/envelope shapes."
new = "`src/contract_schema.rs` owns reusable externally visible semantic shapes used directly by MCP and by parity tests for the HTTP/UTCP projections. It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, execution receipt, execution-audit integrity, authorization-policy integrity, and the authorization-policy snapshot records/envelope (`DurableGrantSnapshot`, `DelegatedGrantSnapshot`, `AuthorizationPolicySnapshot`, and `AuthorizationPolicyEnvelope`)."
s = replace_once(s, old, new, "contract schema inventory paragraph")
addition = r'''

Authorization-policy inventory has one canonical Rust data source: `authorization::read_authorization_policy_snapshot(conn)`. REST `GET /api/authorization-policy`, MCP `blackboard_authorization_policy`, OpenAPI, and UTCP are projections only. The adapters do not enumerate `principal_grants` or `delegated_grants` themselves. Visibility is guarded independently by `read_authorization_policy` on the global `authorization-policy` resource, so inventory access cannot be inferred from integrity, execution-audit, or administration authority.

For MCP, authentication metadata may change the transport schema without changing the semantic tool. Modern Streamable HTTP with configured OIDC may omit body HMAC `auth` because the HTTP Bearer header authenticates the transport principal; legacy HTTP and stdio retain the participant-HMAC `auth` requirement. Both forms reach the same Blackboard authorization kernel and the same canonical snapshot reader.
'''
if "Authorization-policy inventory has one canonical Rust data source" not in s:
    anchor = "\n## Generation and validation rule\n"
    if anchor not in s:
        raise SystemExit("contract-projections generation anchor missing")
    s = s.replace(anchor, addition + anchor, 1)
p.write_text(s, encoding="utf-8")
