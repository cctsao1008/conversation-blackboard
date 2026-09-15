# Scoped Authorization Policy

Conversation Blackboard separates authentication, authorization, semantic intent, and durable attribution.

## Core model

Authorization is evaluated as:

```text
Principal × Capability × Participant × Resource × Context -> AuthorizationDecision
```

`participant_id` remains the durable Blackboard attribution identity. Authentication mechanisms resolve an actor into a `Principal`; the authorization layer then determines whether that principal may exercise a capability for the participant and resource.

The canonical decision contains only non-secret policy metadata:

```text
allowed
source
reason
optional grant_id
optional consume_grant_id
```

Credential material such as JWTs, bearer tokens, HMAC secrets, TOTP values, or session secrets is never part of the decision object.

## Authority sources

Three authority sources coexist:

1. **Explicit durable scoped grants** in `principal_grants`.
2. **Implicit compatibility authority** derived from existing participant relationships:
   - GitHub owner binding (`owner_provider = github`, matching `owner_subject`).
   - participant HMAC self-authentication.
   - human-web self-authentication, with `manage_channels` limited to admin participants.
3. **Delegated grants** in `delegated_grants`, optionally constrained by resource, semantic `intent_id`, expiry, and one-shot consumption.

Explicit durable grants are evaluated per capability. If one or more active explicit grants exist for a principal + participant + capability, only matching resource grants authorize through the durable-grant path; broader implicit fallback does not bypass the restriction. Intent-aware delegated authority may still authorize a separately scoped semantic execution when its own constraints match.

## Lifecycle precedence

Participant lifecycle remains authoritative. A missing participant is denied. An inactive participant denies all durable, implicit, and delegated authority.

## Canonical decision kernel

`authorization::evaluate_authorization_current_schema` is the single policy kernel that owns participant lifecycle, durable grant matching, implicit compatibility authority, delegated constraints, expiry, intent binding, one-shot replay classification, and consumption intent. It is reached through two deliberately different wrappers:

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

Both wrappers return the same `AuthorizationDecision`; only execution may act on `consume_grant_id`. `authorize` remains a thin boolean compatibility projection over the execution-compatible wrapper.

## One-shot delegated authority

A fresh matching one-shot delegated grant returns `consume_grant_id`. Only the execution layer may consume it, and consumption remains in the same SQLite transaction as semantic effect, ingress provenance, and execution receipt.

A committed replay of the same semantic intent is classified separately and does not request a second consumption. A consumed grant without a matching committed execution receipt is denied.

## Explainability

`conversation-blackboard grant explain` and the privileged remote decision projections render the canonical decision without mutating authority. Their decision source/reason therefore cannot drift into a second policy implementation.

Remote decision visibility is independently privileged:

```text
capability = read_authorization_decision
resource   = authorization-decision
```

The authenticated caller `Principal` is distinct from the target `Principal` whose authorization context is being explained. Caller authorization is evaluated first; only then is the target principal/participant/capability/resource/intent context passed to the read-only canonical evaluator. A target denial remains successful explanation data after caller authorization succeeds.

REST uses `GET /api/authorization-decision/explain` with the target context in query parameters. This is intentional: participant-HMAC HTTP authentication binds the complete `method + path_and_query`, so the authenticated request covers the exact decision query without introducing a new body-signing protocol.

MCP exposes `blackboard_authorization_decision`. Modern HTTP MCP with a verified OIDC Bearer authenticates the caller at the transport boundary and may omit body HMAC `auth`. Legacy HTTP and stdio retain participant-HMAC authentication, but this tool uses a dedicated canonical proof payload that binds the complete normalized target principal, target participant, capability, resource, and intent. A proof for one decision query therefore cannot be replayed for another target context.

All explanation paths are observational. They use the read-only evaluator, do not consume one-shot grants, do not create or repair grant storage, and expose `consume_on_commit` rather than an internal consumption identifier.

Representative reason classes include:

- `participant_missing`
- `participant_inactive`
- `explicit_durable_grant_match`
- `explicit_resource_scope_mismatch`
- `implicit_github_owner`
- `implicit_participant_hmac`
- `implicit_human_web`
- `implicit_human_web_admin`
- `delegated_grant_match`
- `delegated_resource_mismatch`
- `delegated_intent_mismatch`
- `delegated_grant_expired`
- `delegated_consumed_different_intent`
- `delegated_committed_replay`
- `delegated_consumed_without_committed_receipt`
- `no_matching_authority`

## Access context

`/api/access-context` reports effective grants derived from the same authorization model. It exposes authorization state, not credentials.


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


Decision/explain visibility is a third independent observational surface. Only Human Web admin self receives implicit global explain authority; participant-HMAC self, GitHub-owner compatibility authority, and OIDC/Bearer identity require an explicit `read_authorization_decision` grant. Explain authority does not imply policy snapshot, policy integrity, execution audit/sweep, or channel administration, and those capabilities do not imply explain visibility.

## Authorization administration

Authorization mutation is owned by `authorization_admin`, not by CLI, REST, MCP, or the read-side policy evaluator. The supported mutation path is:

```text
local operator CLI
        ↓
authorization_admin canonical service
        ↓
validated durable/delegated lifecycle mutation
        +
append-only authorization_admin_events provenance
        ↓
one SQLite transaction
```

The canonical service preserves existing lifecycle behavior: an exact active durable scope is idempotent, an exact inactive durable scope reactivates the same row, durable deactivation closes every active row in the same exact legacy scope, delegated create remains append-only, and delegated deactivation is idempotent. Execution-time one-shot consumption is not an administration mutation and remains owned by the semantic execution transaction.

Only an **effective policy state change** emits an administration event. A durable create that resolves to an already-active exact grant and a repeated deactivation that changes zero rows do not create misleading mutation records.

Administration provenance contains only non-secret actor and policy metadata: administration surface, optional authenticated `Principal`, optional actor participant attribution, target principal, participant, capability/resource/intent scope, lifecycle transition, and commit time. Local CLI administration is represented explicitly as `local-cli` with no fabricated remote principal. Authentication credentials are never written to administration evidence.

Schema ownership is explicit. `db init` owns administration-schema creation/migration. Mutation services require a current writable schema and fail with migration-required semantics rather than repairing storage themselves. Read-side commands likewise remain observational: `grant list`, `grant durable list`, `grant explain`, `grant verify`, and `grant history` open/read current state without calling grant-schema migration.

`authorization_admin::read_administration_events(conn, ...)` is the canonical local provenance reader. `conversation-blackboard grant history` is its operator projection. REST and MCP have no grant mutation endpoint/tool; source regressions protect that boundary so remote adapters cannot silently become a second administration implementation.

## Verification boundary

Authorization changes are accepted only after formatting, strict Clippy, Rust tests, release build, and Windows service/CLI/smoke/package verification pass in core CI. The #81 migration additionally verifies that execution and `grant explain` consume one canonical policy decision while one-shot consumption remains execution-only and atomic.

The #81 implementation and its verification-boundary documentation passed core-ci run `34764521912`: Linux formatting, strict Clippy, tests, and release build passed; Windows release verification, CLI surface, service/operations/two-identity smoke, packaging, and artifact upload also passed.
