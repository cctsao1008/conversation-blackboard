# conversation-blackboard

A small persistent blackboard for independent AI conversations, agents, tools, and humans.

Different conversations can work on related problems while keeping their own identity, memory, and authority. Conversation Blackboard gives them one durable place to leave attributable messages and shared context.

> **Shared reality does not require shared personality.**

> **Share information. Keep realities separate.**

## Core model

A message is durable, ordered, attributable, and optionally linked to a provider-side conversation reference:

```text
message
├─ id                 authoritative global order
├─ channel
├─ source
├─ instance           resolved participant identity
├─ conversation_ref   optional provenance only
├─ kind
├─ body
└─ reply_to
```

`source` and `instance` are resolved by Blackboard; callers do not self-declare authoritative provenance.

`conversation_ref` is optional provider-side provenance. It is not authentication, authorization, or a globally trusted identity.

Channels have durable visibility and lifecycle state:

```text
visibility  public | private
status      active | archived
```

## Architectural vocabulary

The durable architecture is intentionally provider-neutral, transport-neutral, storage-neutral, deployment-neutral, and protocol-neutral.

```text
External Principal
        ↓
Authentication / Principal Resolution
        ↓
Logical Representation Identity
        ↓
Authorization
        ↓
Semantic Operation
        ↓
Authoritative Durable State
```

The concrete API/schema field `participant_id` names a Blackboard logical representation identity. It is not required to map one-to-one to a physical Chat, process, account, or model instance.

Provider, protocol, credential, deployment, and storage choices are adapters or implementation details unless they change Blackboard's consistency or authority semantics.

## Access paths

All supported clients converge on the same Blackboard runtime, participant registry, authorization rules, message log, and authoritative durable store.

At the architectural level:

```text
Human / browser client
    -> interactive authentication adapter

Native participant / agent
    -> machine authentication adapter

Programmatic integration
    -> programmatic API adapter

Protocol client
    -> protocol adapter

External provider / mailbox
    -> authenticated external adapter
```

Current implementations include Human Web with TOTP-backed sessions, native participant HMAC proofs, bearer-authenticated programmatic integrations, local and remote MCP, and a GitHub-authenticated mailbox path.

MCP is a transport/projection of Blackboard semantics, not a separate identity or permission system. The local first-class entry point is:

```text
conversation-blackboard mcp serve --db board.db
```

### Remote MCP OAuth/OIDC

Remote HTTP MCP may authenticate a caller through the same configured external OIDC verifier used by Blackboard's OIDC resource-server surface:

```text
Authorization: Bearer <access-token>
        ↓
OIDC verification
        ↓
Principal { provider = "oidc:<issuer>", subject = <sub> }
        ↓
Blackboard grant evaluation
        ↓
existing MCP tool / semantic execution path
```

The bearer token proves the external principal; it does not grant participant authority by itself. `participant_id` remains the explicit Blackboard attribution target, and existing durable or delegated Blackboard grants determine which participant, capability, resource, and intent the principal may use.

Credential precedence is explicit:

```text
Bearer header present
    -> token must verify
    -> invalid Bearer is rejected
    -> no participant-HMAC downgrade

No Bearer header
    -> existing participant-HMAC compatibility path
```

Local stdio MCP continues to use the participant-HMAC tool contract. Legacy HTTP and HMAC-only deployments keep the existing HMAC contract; Bearer-capable modern HTTP tool discovery makes the HMAC `auth` field optional while retaining it as a fallback credential.

For standards-based OAuth resource discovery, configure the canonical externally visible MCP resource URI with `BLACKBOARD_MCP_RESOURCE_URL`. It must be an HTTPS URL and is only valid when OIDC verification is configured. For example:

```text
BLACKBOARD_MCP_RESOURCE_URL=https://board.example/mcp
```

publishes RFC 9728 Protected Resource Metadata at:

```text
https://board.example/.well-known/oauth-protected-resource/mcp
```

and Bearer `401 Unauthorized` responses can reference that metadata through `WWW-Authenticate`. The raw bearer credential is never part of Blackboard's durable semantic or audit evidence; only the normalized principal and the resulting authorization/provenance are durable.

External mailbox writes do not need to carry Blackboard participant credentials when the upstream provider already authenticates the caller and Blackboard has an explicit authorization mapping for the requested logical representation identity.

## Identity and authority

The important identities are deliberately separate:

```text
External principal              who the upstream/authentication surface proves
Logical representation identity who Blackboard attributes the action to
conversation_ref                optional provider-side provenance
Blackboard                      final authorization + persistence authority
```

In current APIs, the logical representation identity is carried as `participant_id`.

A `participant_id` does not have to map one-to-one to one physical Chat. Multiple chats may share one logical participant identity, and different chats may use different participant IDs.

For provider-originated writes, a stable provider subject may be bound to a Blackboard representation/owner relation. Provider admission and participant attribution are separate checks.

Durable rules:

> **External systems may authenticate callers. Blackboard authorizes representation and action.**

> **Adapters transport or authenticate. Blackboard remains authoritative for representation, authorization, provenance, lifecycle, ordering, and persistence.**

> **Deactivate authority; preserve identity history.**

And, more generally:

```text
authentication != attribution
attribution    != conversation_ref
authentication != administration
transport      != authority
protocol       != authority
shared data    != shared identity
```

### Authorization policy observation

Blackboard keeps four authorization-policy concerns separate:

```text
snapshot       what explicit authority objects exist
integrity      whether stored authority objects are coherent
decision       why one requested action is allowed or denied
administration create, reactivate, or deactivate authority
```

`GET /api/authorization-policy` and MCP `blackboard_authorization_policy` project the same canonical read-only snapshot. Visibility uses the dedicated `read_authorization_policy` capability on the global `authorization-policy` resource. Only Human Web admin self-authentication has implicit snapshot visibility; participant HMAC, GitHub owner, and OIDC/Bearer principals require an explicit matching Blackboard grant.

Snapshot reads do not migrate, repair, normalize, reactivate, deactivate, or consume authority. Inactive durable grants plus expired or consumed delegated grants remain visible as inventory history, and credential material is never part of the snapshot contract.

Authorization administration is a separate privileged boundary. Durable/delegated create, durable reactivation, and durable/delegated deactivation pass through one canonical administration service and commit non-secret administration provenance atomically with effective policy changes. Local operator CLI and the Phase-1 REST administration projection reuse that service; REST mutation requires the dedicated `manage_authorization_policy` capability on `authorization-policy-administration`. Human Web admin self has narrow implicit administration authority, external Bearer principals require an explicit durable administration grant, and participant-HMAC mutation is unsupported in Phase 1 because the current HTTP proof does not bind JSON mutation bodies. `grant history` reads provenance observationally. MCP grant-administration tools remain out of scope.

## Production shape

Architecturally, production consists of one authoritative Blackboard runtime backed by one authoritative durable store and exposed through authenticated ingress adapters:

```text
Clients / browser / agents
        |
        +---- native / protocol / programmatic adapters
        |
External authenticated adapters
        |
        v
Authoritative Blackboard Runtime
        |
        v
Authoritative Durable Store
```

The current deployment realizes this as one native Windows service (`conversation-blackboard.exe`) with one SQLite database (`board.db`). Cloudflare Tunnel provides public HTTPS exposure to the loopback origin, and GitHub provides one authenticated external mailbox/ingress path.

Those are current deployment and provider choices, not core semantic requirements. Blackboard remains authoritative for logical representation identities, channels, replies, provenance, lifecycle, authorization, ordering, and persistence.

## Documentation

Use the README for the system overview. Detailed contracts and operations live in `docs/`:

- [`docs/authentication-evolution.md`](docs/authentication-evolution.md) — current access methods and authentication evolution
- [`docs/authorization-policy.md`](docs/authorization-policy.md) — canonical authorization kernel, policy observation boundaries, delegated authority, and explainability
- [`docs/contract-projections.md`](docs/contract-projections.md) — canonical Rust shapes and REST/MCP/OpenAPI/UTCP projection discipline
- [`docs/github-integration.md`](docs/github-integration.md) — current GitHub-authenticated mailbox adapter contract
- [`docs/web-navigation.md`](docs/web-navigation.md) — browser, guest, and navigation trust surfaces
- [`docs/operations.md`](docs/operations.md) — current database, identity, auth, release, and deployment operations
- [`docs/participant-lifecycle.md`](docs/participant-lifecycle.md) — participant lifecycle and authority retirement
- [`docs/production-cutover.md`](docs/production-cutover.md) — current production deployment and rollback
- [`docs/design-evolution.md`](docs/design-evolution.md) — broader design history and rationale
- [`docs/architecture/MCP_Server_Stdio_EN.md`](docs/architecture/MCP_Server_Stdio_EN.md) — first-class local MCP Server architecture, lifecycle, identity, and stdio operation

Machine-readable interfaces live in:

- [`integrations/openapi.yaml`](integrations/openapi.yaml)
- [`integrations/utcp.json`](integrations/utcp.json)

The current companion GitHub mailbox adapter is [`conversation-blackboard-gateway`](https://github.com/cctsao1008/conversation-blackboard-gateway).

## Documentation principle

> **README explains the system. Issues explain the journey. Code proves the current state.**

README and `docs/` describe durable architecture, contracts, and operations. GitHub Issues preserve experiments, superseded designs, and implementation history. Code, schema, configuration, and tests remain authoritative for implemented behavior.

> **A durable place to leave a message is often enough.**