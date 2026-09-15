from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# Domain authority isolation: explain capability is neither implied by nor implies
# neighboring privileged observation/administration capabilities.
p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")
anchor = '''    #[test]\n    fn authorization_policy_snapshot_reads_full_lifecycle_without_filtering() {'''
test = r'''    #[test]
    fn authorization_decision_authority_is_isolated_from_other_privileged_capabilities() {
        let (_dir, conn) = setup();
        let decision_reader = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "decision-reader".to_owned(),
        };
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                &decision_reader.provider,
                &decision_reader.subject,
                READ_AUTHORIZATION_DECISION,
                AUTHORIZATION_DECISION_RESOURCE
            ],
        )
        .unwrap();
        assert!(authorize(
            &conn,
            &decision_reader,
            "maker-main",
            READ_AUTHORIZATION_DECISION,
            Some(AUTHORIZATION_DECISION_RESOURCE),
        )
        .unwrap());
        for (capability, resource) in [
            (READ_AUTHORIZATION_POLICY, Some(AUTHORIZATION_POLICY_RESOURCE)),
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
                !authorize(&conn, &decision_reader, "maker-main", capability, resource).unwrap(),
                "decision authority leaked into {capability}"
            );
        }

        let other_reader = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "other-reader".to_owned(),
        };
        for (capability, resource) in [
            (READ_AUTHORIZATION_POLICY, Some(AUTHORIZATION_POLICY_RESOURCE)),
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
            conn.execute(
                "INSERT INTO principal_grants
                    (principal_provider, principal_subject, participant_id, capability, resource)
                 VALUES (?1, ?2, 'maker-main', ?3, ?4)",
                params![&other_reader.provider, &other_reader.subject, capability, resource],
            )
            .unwrap();
        }
        assert!(!authorize(
            &conn,
            &other_reader,
            "maker-main",
            READ_AUTHORIZATION_DECISION,
            Some(AUTHORIZATION_DECISION_RESOURCE),
        )
        .unwrap());
    }

'''
if "authorization_decision_authority_is_isolated_from_other_privileged_capabilities" in s:
    raise SystemExit("decision authority isolation regression already exists")
s = replace_once(s, anchor, test + anchor, "decision isolation test anchor")
p.write_text(s, encoding="utf-8")

# Adapter guard: adapters may normalize transport inputs, but policy evaluation and
# reason classification stay in authorization.rs.
p = Path("src/contract_parity_tests.rs")
s = p.read_text(encoding="utf-8")
if "authorization_decision_adapters_reuse_canonical_evaluator_without_policy_sql" in s:
    raise SystemExit("decision adapter guard already exists")
s += r'''

#[test]
fn authorization_decision_adapters_reuse_canonical_evaluator_without_policy_sql() {
    let adapters = [
        ("http", include_str!("access_api.rs")),
        ("mcp", include_str!("mcp.rs")),
    ];
    for (name, source) in adapters {
        assert!(
            source.contains("normalize_authorization_decision_target"),
            "{name} adapter must reuse canonical authorization decision target normalization"
        );
        assert!(
            source.contains("explain_authorization"),
            "{name} adapter must reuse the canonical read-only authorization evaluator"
        );
        for forbidden in [
            "FROM principal_grants",
            "FROM delegated_grants",
            "JOIN principal_grants",
            "JOIN delegated_grants",
            "explicit_durable_grant_match",
            "explicit_resource_scope_mismatch",
            "delegated_grant_expired",
            "delegated_intent_mismatch",
            "no_matching_authority",
        ] {
            assert!(
                !source.contains(forbidden),
                "{name} adapter must not reconstruct authorization policy/reason logic: {forbidden}"
            );
        }
    }
}
'''
p.write_text(s, encoding="utf-8")

# Durable architecture documentation.
p = Path("docs/authorization-policy.md")
s = p.read_text(encoding="utf-8")
old = '''`authorization::evaluate_authorization` is the policy kernel for both execution and operator explanation. It owns participant lifecycle, durable grant matching, implicit compatibility authority, delegated constraints, expiry, intent binding, one-shot replay classification, and consumption intent.

```text
authenticated Principal
        ↓
authorization::evaluate_authorization
        ↓
AuthorizationDecision
        ├─ execution.rs -> enforce allow/deny and consume only on commit
        └─ grant explain -> render the same decision read-only
```

`authorize` remains a thin compatibility projection for non-intent boolean checks. Intent-aware execution consumes the canonical decision directly.'''
new = '''`authorization::evaluate_authorization_current_schema` is the single policy kernel that owns participant lifecycle, durable grant matching, implicit compatibility authority, delegated constraints, expiry, intent binding, one-shot replay classification, and consumption intent. It is reached through two deliberately different wrappers:

```text
execution / compatibility
    -> authorization::evaluate_authorization
       -> may ensure the current grant schema
       -> shared current-schema decision kernel

observation / explanation
    -> authorization::explain_authorization
       -> requires the schema to already be current
       -> never migrates or repairs
       -> same shared current-schema decision kernel
```

Both wrappers return the same `AuthorizationDecision`; only execution may act on `consume_grant_id`. `authorize` remains a thin boolean compatibility projection over the execution-compatible wrapper.'''
s = replace_once(s, old, new, "canonical decision kernel documentation")

old = '''`conversation-blackboard grant explain` renders the canonical decision without mutating authority. Its decision source/reason therefore cannot drift into a second policy implementation.
'''
new = '''`conversation-blackboard grant explain` and the privileged remote decision projections render the canonical decision without mutating authority. Their decision source/reason therefore cannot drift into a second policy implementation.

Remote decision visibility is independently privileged:

```text
capability = read_authorization_decision
resource   = authorization-decision
```

The authenticated caller `Principal` is distinct from the target `Principal` whose authorization context is being explained. Caller authorization is evaluated first; only then is the target principal/participant/capability/resource/intent context passed to the read-only canonical evaluator. A target denial remains successful explanation data after caller authorization succeeds.

REST uses `GET /api/authorization-decision/explain` with the target context in query parameters. This is intentional: participant-HMAC HTTP authentication binds the complete `method + path_and_query`, so the authenticated request covers the exact decision query without introducing a new body-signing protocol.

MCP exposes `blackboard_authorization_decision`. Modern HTTP MCP with a verified OIDC Bearer authenticates the caller at the transport boundary and may omit body HMAC `auth`. Legacy HTTP and stdio retain participant-HMAC authentication, but this tool uses a dedicated canonical proof payload that binds the complete normalized target principal, target participant, capability, resource, and intent. A proof for one decision query therefore cannot be replayed for another target context.

All explanation paths are observational. They use the read-only evaluator, do not consume one-shot grants, do not create or repair grant storage, and expose `consume_on_commit` rather than an internal consumption identifier.
'''
s = replace_once(s, old, new, "explainability documentation")

anchor = '''The snapshot contract contains only non-secret authority metadata. Bearer/JWT values, HMAC secrets, TOTP material, session credentials, and participant authentication secrets are outside the snapshot model.\n'''
addition = r'''

Decision/explain visibility is a third independent observational surface. Only Human Web admin self receives implicit global explain authority; participant-HMAC self, GitHub-owner compatibility authority, and OIDC/Bearer identity require an explicit `read_authorization_decision` grant. Explain authority does not imply policy snapshot, policy integrity, execution audit/sweep, or channel administration, and those capabilities do not imply explain visibility.
'''
if "Decision/explain visibility is a third independent observational surface" not in s:
    s = replace_once(s, anchor, anchor + addition, "policy observation boundary")
p.write_text(s, encoding="utf-8")

p = Path("docs/contract-projections.md")
s = p.read_text(encoding="utf-8")
old = '''`src/contract_schema.rs` owns reusable externally visible semantic shapes used directly by MCP and by parity tests for the HTTP/UTCP projections. It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, execution receipt, execution-audit integrity, authorization-policy integrity, and the authorization-policy snapshot records/envelope (`DurableGrantSnapshot`, `DelegatedGrantSnapshot`, `AuthorizationPolicySnapshot`, and `AuthorizationPolicyEnvelope`).'''
new = '''`src/contract_schema.rs` owns reusable externally visible semantic shapes used directly by MCP and by parity tests for the HTTP/UTCP projections. It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, execution receipt, execution-audit integrity, authorization-policy integrity, authorization-policy snapshot records/envelope, and authorization-decision explanation/envelope shapes.'''
s = replace_once(s, old, new, "contract shared shape inventory")

anchor = '''For MCP, authentication metadata may change the transport schema without changing the semantic tool. Modern Streamable HTTP with configured OIDC may omit body HMAC `auth` because the HTTP Bearer header authenticates the transport principal; legacy HTTP and stdio retain the participant-HMAC `auth` requirement. Both forms reach the same Blackboard authorization kernel and the same canonical snapshot reader.\n'''
addition = r'''

Authorization decision explanation has one canonical read-only evaluator: `authorization::explain_authorization(conn, ...)`, returning the same decision semantics used by execution without schema migration or delegated-grant consumption. REST `GET /api/authorization-decision/explain`, MCP `blackboard_authorization_decision`, OpenAPI, and UTCP project that result. Caller authority is separate from the target Principal being evaluated. REST participant-HMAC binds the target context through the full query-bearing request target; MCP participant-HMAC uses a decision-specific canonical proof binding the complete normalized target context. OIDC/Bearer MCP callers remain authenticated at the HTTP transport boundary. Adapters never classify policy reasons or enumerate grant tables for explanation.
'''
if "Authorization decision explanation has one canonical read-only evaluator" not in s:
    s = replace_once(s, anchor, anchor + addition, "contract decision projection docs")
p.write_text(s, encoding="utf-8")
