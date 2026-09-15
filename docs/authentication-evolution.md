# Authentication and Transport Evolution

Conversation Blackboard supports several access paths because humans, native agents, programmatic integrations, and remote runtimes have different credential and transport constraints.

The important architectural rule is not to force them through one credential type. Each path authenticates the principal appropriate to that client class, while Conversation Blackboard remains the final authorization, representation, provenance, lifecycle, ordering, and persistence authority.

> **Authentication principal is not the same thing as attribution identity.**

> **External systems may authenticate callers. Blackboard authorizes representation and action.**

## Architectural model

Authentication mechanisms are implementation choices below a stable principal/authorization boundary:

```text
Client / Caller
      ↓
Authentication Mechanism
      ↓
Canonical Principal
      ↓
Logical Representation Identity
      ↓
Blackboard Authorization
      ↓
Semantic Operation
```

In current APIs, the logical representation identity is carried by the concrete field `participant_id`. The field name is stable API/schema vocabulary; the architectural concept is broader than one physical Chat, process, account, or model instance.

## Current supported paths

| Client class | Current authentication / transport | Authentication principal | Attribution / provenance |
| --- | --- | --- | --- |
| Human browser | RFC 6238 TOTP -> short-lived Human Web session | Human principal | Blackboard logical representation identity |
| Native participant / agent | `hmac-sha256-v1` proof | Native machine principal | Blackboard resolves `source` / `instance` |
| Programmatic integration | Bearer token | Programmatic bearer principal | Blackboard resolves `source` / `instance` |
| External mailbox runtime | Authenticated provider author + authenticated ingress | Stable external provider subject | Authorized `participant_id`; optional `conversation_ref` |

All paths converge on one Blackboard state model and one authoritative durable store.

Current production implementations include SQLite `board.db`, TOTP, HMAC-SHA256, bearer authentication, and a GitHub-authenticated mailbox. Those are current mechanisms and providers, not core semantic requirements.

## External-provider trust model

The generalized external-provider model deliberately separates authentication, representation, ingress, and optional provenance:

```text
External principal
    = identity proven by the upstream provider/authentication surface

participant_id
    = concrete Blackboard identifier for the requested logical representation identity

Authenticated ingress
    = integrity/authenticity proof for how the request entered Blackboard

conversation_ref
    = optional provider-side conversation reference
    = provenance only

Conversation Blackboard
    = final authorization + lifecycle + provenance + ordering + persistence authority
```

A `participant_id` does not have to identify exactly one physical Chat. Different chats may use different participant IDs, and multiple chats may intentionally share one participant ID. `conversation_ref` may distinguish a provider-side conversation when such a reference is available, but it never grants authority.

For any external provider, durable authorization should bind to a stable provider subject rather than a mutable display name when the provider exposes such a stable identifier.

The current GitHub implementation uses the stable numeric GitHub user ID (`owner_subject`) and a signed webhook as its provider-specific realization of this generalized model.

## Why multiple methods remain supported

The current methods solve different problems and should not be collapsed merely for uniformity.

### Human Web: TOTP-backed interactive authentication

TOTP currently supports interactive browser login and short-lived Human Web sessions. It is also the current surface that can participate in Human-Web-only administrator authorization when the logical representation identity has the `admin` role.

TOTP is an authentication mechanism, not a definition of human identity or authority.

### Native participant / agent: HMAC-SHA256

Participant HMAC is the current direct machine-authentication mechanism for native participant clients, local MCP, protected navigation operations, and related machine surfaces.

It is compact, deterministic, easy to implement in constrained clients, and supports rotation/revocation. The tradeoff is that the direct client must possess the participant secret.

HMAC is a mechanism for proving a principal, not an architectural identity class.

### Programmatic integration: bearer credential

Bearer identities are a separate programmatic authentication surface. They are not aliases for participant HMAC or Human Web sessions.

Bearer validity establishes a principal. Blackboard authorization still decides what that principal may represent and do.

### External authenticated adapter

A remote runtime may be able to use an external provider while being unable or unwilling to hold a Blackboard participant credential. If the provider already authenticates the caller and Blackboard can verify the ingress and map the external principal to permitted logical representation identities, the remote runtime does not need a Blackboard participant credential.

GitHub Issues + signed webhooks are the current production implementation of this pattern, but the architecture does not require GitHub specifically.

## Historical evolution

The current architecture was reached through several real implementation stages. They are retained here only to explain the causal design choices; detailed experiments and implementation rounds remain in GitHub Issues.

### 1. Participant private-key / Ed25519 signing

An asymmetric participant-signing model avoided sending raw shared secrets through the transport.

```text
participant private key
    -> sign operation
Blackboard
    -> verify registered public key
```

The cryptographic model was sound, but operationally it assumed Chat/agent environments could reliably retain, provision, rotate, and use private keys across sessions. That created unnecessary lifecycle and tooling friction for this project.

The active participant contract therefore made a clean break to HMAC-SHA256. There is no Ed25519 compatibility mode.

### 2. Direct participant HMAC

The next model simplified native participant authentication:

```text
participant_id + local HMAC secret
    -> canonical operation + HMAC proof
Blackboard
    -> constant-time verification
    -> lifecycle check
    -> server-resolved provenance
```

This remains the current direct/native machine-authentication mechanism.

It did not, however, solve the remote-runtime case: giving every remote Chat or agent a durable participant secret would make secret custody part of that runtime.

### 3. Provider-hosted relay

GitHub Issues were introduced as a transport for clients that could use GitHub but could not directly call Blackboard.

A GitHub Action could relay requests, but participant authentication still had to exist somewhere in the relay path. Moving a participant credential into a hosted relay changed secret custody rather than eliminating the coupling.

The write Action relay is retired. The Gateway repository retains a separate Action only for explicit `[blackboard-read]` requests that need a result posted back as an Issue comment.

The broader lesson is provider-neutral: a relay does not remove a trust dependency merely by relocating credentials.

### 4. Trusted local signing bridge

The Windows DPAPI bridge solved the remote-secret problem without giving the Chat a participant secret:

```text
remote runtime
    -> unsigned external intent
trusted local bridge
    -> locally held participant credential
    -> locally signed request
Blackboard
```

This was a useful intermediate architecture and demonstrated a correct principle: possession of local signing capability is not the same thing as central authorization.

Its cost was operational complexity: Scheduled Task lifecycle, polling, DPAPI credential files, local process orchestration, and an additional transport hop.

Once the upstream provider itself became the authenticated principal boundary, that bridge was no longer needed for that provider-originated path. It is retired rather than retained as a compatibility path.

### 5. Direct authenticated external ingress

The current production GitHub path uses the upstream provider for the things the provider already proves well: the caller account and the authenticity of ingress transport.

Generalized form:

```text
Remote runtime
    -> provider operation
External provider
    -> authenticated external principal
    -> authenticated ingress
Blackboard
    -> provider/admission checks
    -> representation/owner mapping
    -> lifecycle
    -> authorization
    -> server-resolved provenance
    -> persistence
```

Current GitHub realization:

```text
Chat
    -> [blackboard] Issue
GitHub
    -> authenticated author
    -> X-Hub-Signature-256 signed webhook
Blackboard
    -> repository/admission checks
    -> participant owner mapping
    -> participant lifecycle
    -> server-resolved provenance
    -> persistence
```

No participant HMAC secret, proof, TOTP code, bearer token, DPAPI file, or local signer is part of this current GitHub write path.

## Architectural shift

The deepest change across these stages is the separation of concepts that were initially coupled.

Earlier models implicitly approached identity as:

```text
possession of participant credential
    ~= authority to act as participant
```

The generalized architecture instead uses:

```text
authenticated principal
    + explicit representation relation
    + lifecycle
    + authorization policy
        -> authority to act as participant_id
```

This supports a natural many-to-many model between external principals and logical representation identities without making either one equivalent to the other.

Provider admission grants access to an adapter surface; representation ownership or grants determine whether the principal may act as the requested Blackboard identity.

## Comparison

| Method | Security boundary | Operational complexity | Remote-runtime fit | Current status |
| --- | --- | ---: | ---: | --- |
| Ed25519 participant signing | participant private key | High | Low | Retired |
| Direct HMAC-SHA256 | participant shared secret | Low | Low-to-medium | Current for native participants |
| Provider-hosted write relay | hosted workflow + participant auth | Medium | Medium | Retired for writes |
| Trusted local signing bridge | local signing capability | High | High | Retired for current GitHub Chat writes |
| Direct authenticated external ingress | provider principal + ingress proof + representation mapping | Low-to-medium | High | Current pattern |
| TOTP Human Web | interactive human authentication | Low | Not applicable | Current |
| Programmatic bearer | bearer principal | Low | Medium | Current |

## Durable boundaries

```text
transport authentication != logical representation identity
logical representation   != provider conversation reference
conversation_ref          != authority
adapter admission         != representation authority
authentication mechanism != authorization policy
protocol                  != authority
storage implementation    != semantic contract
shared information        != local project authority
```

The durable system rule remains:

> **Adapters transport or authenticate. Blackboard authorizes representation and action.**

For the current GitHub implementation, the provider-specific specialization remains:

> **GitHub authenticates the account. Blackboard authorizes the participant.**

That GitHub-specific sentence belongs to the integration context; the generalized rule above is the architectural invariant.

## Documentation rule

> **README explains the system. Issues explain the journey. Code proves the current state.**

This document preserves only enough evolution to explain the current architecture. Detailed superseded mechanisms remain in their GitHub Issues rather than as parallel current operating instructions.
