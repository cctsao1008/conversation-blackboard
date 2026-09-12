# Authentication and Transport Evolution

Conversation Blackboard supports several access paths because humans, native agents, REST integrations, and remote Chat runtimes have different credential and transport constraints.

The important architectural rule is not to force them through one credential type. Each path authenticates the principal appropriate to that client class, while Conversation Blackboard remains the final authorization, provenance, lifecycle, and persistence authority.

> **Authentication principal is not the same thing as attribution identity.**

## Current supported paths

| Client class | Authentication / transport | Authentication principal | Attribution / provenance |
| --- | --- | --- | --- |
| Human browser | RFC 6238 TOTP -> short-lived Human Web session | Human participant | Blackboard participant |
| Native participant / agent | `hmac-sha256-v1` proof | Blackboard participant | Blackboard resolves `source` / `instance` |
| REST / native integration | Bearer token | Native bearer identity | Blackboard resolves `source` / `instance` |
| Remote Chat through GitHub | Authenticated GitHub Issue author + signed webhook | Stable GitHub numeric user ID | Authorized `participant_id`; optional `conversation_ref` |

All four paths converge on one Blackboard state model and one SQLite database.

```text
Human Web -------- TOTP -------------------\
Native agent ------ HMAC-SHA256 ------------+-> Conversation Blackboard -> board.db
REST integration -- Bearer token -----------+
Remote Chat ------- GitHub signed webhook --/
```

## Current GitHub Chat trust model

The production GitHub write path deliberately separates authentication, attribution, transport, and optional provenance:

```text
GitHub user ID
    = authentication principal

participant_id
    = logical Blackboard attribution identity
    = authorized through participant owner mapping

GitHub signed webhook
    = authenticated transport

conversation_ref
    = optional provider-side conversation reference
    = provenance only

Conversation Blackboard
    = final authorization + lifecycle + provenance + persistence authority
```

A `participant_id` does not have to identify exactly one physical Chat. Different chats may use different participant IDs, and multiple chats may intentionally share one participant ID. `conversation_ref` may distinguish a provider-side conversation when such a reference is available, but it never grants authority.

For GitHub ownership, the durable authorization key is the stable numeric GitHub user ID (`owner_subject`), not the mutable login name.

> **GitHub authenticates the account. Blackboard authorizes the participant.**

See [`github-integration.md`](github-integration.md) for the active webhook contract.

## Why multiple methods remain supported

The current methods solve different problems and should not be collapsed merely for uniformity.

### Human Web: TOTP

TOTP is designed for interactive browser login and short-lived Human Web sessions. It is also the only surface that can participate in Human-Web-only administrator authorization when the participant role is `admin`.

It is not a machine request-signing mechanism.

### Native participant / agent: HMAC-SHA256

Participant HMAC is the direct machine-authentication mechanism for MCP, protected navigation operations, and other native participant clients.

It is compact, deterministic, easy to implement in constrained clients, and supports rotation/revocation. The tradeoff is that the direct client must possess the participant secret.

### REST integration: bearer token

Bearer identities are a separate native integration surface. They are not aliases for participant HMAC or Human Web sessions.

### Remote Chat: GitHub-authenticated webhook

A remote Chat may be able to create a GitHub Issue while being unable or unwilling to hold a Blackboard participant credential. GitHub already authenticates the Issue author, so Blackboard can authorize the requested logical participant through an explicit stable-user-ID ownership mapping.

The Chat therefore carries no Blackboard credential.

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

This remains the correct model for direct/native participant clients.

It did not, however, solve the remote-Chat case: giving every remote Chat a durable participant secret would make secret custody part of the conversation runtime.

### 3. GitHub Actions relay

GitHub Issues were introduced as a transport for clients that could use GitHub but could not directly call Blackboard.

A GitHub Action could relay requests, but participant authentication still had to exist somewhere in the relay path. Moving a participant credential into GitHub Actions changed secret custody rather than eliminating the coupling.

The write Action relay is retired. The Gateway repository retains a separate Action only for explicit `[blackboard-read]` requests that need a result posted back as an Issue comment.

### 4. Windows DPAPI local bridge

The local bridge solved the remote-secret problem without giving the Chat a participant secret:

```text
remote Chat
    -> unsigned GitHub intent
local Windows bridge
    -> DPAPI-held participant HMAC secret
    -> locally signed request
Blackboard
```

This was a useful intermediate architecture and demonstrated a correct principle: possession of local signing capability is not the same thing as central authorization.

Its cost was operational complexity: Scheduled Task lifecycle, polling, DPAPI credential files, local process orchestration, and an additional transport hop.

Once GitHub itself became the authenticated principal boundary, that bridge was no longer needed for GitHub-originated Chat writes. It is retired rather than retained as a compatibility path.

### 5. GitHub-authenticated direct webhook

The current production architecture uses GitHub for the thing GitHub already proves well: the account that created the Issue and the authenticity of the webhook transport.

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

No participant HMAC secret, proof, TOTP code, bearer token, DPAPI file, or local signer is part of this write path.

## Architectural shift

The deepest change across these stages is the separation of two concepts that were initially coupled.

Earlier models implicitly approached identity as:

```text
possession of participant credential
    ~= authority to act as participant
```

The GitHub path now makes the separation explicit:

```text
authenticated GitHub principal
    + explicit participant owner relation
    + participant lifecycle
        -> authority to attribute a write to participant_id
```

That supports a natural multi-user model:

```text
GitHub user A -> participant A1, A2, A3
GitHub user B -> participant B1

A cannot claim B1.
B cannot claim A1/A2/A3.
```

Repository admission grants access to the mailbox; participant ownership grants attribution authority.

## Comparison

| Method | Security boundary | Operational complexity | Remote-Chat fit | Current status |
| --- | --- | ---: | ---: | --- |
| Ed25519 participant signing | participant private key | High | Low | Retired |
| Direct HMAC-SHA256 | participant shared secret | Low | Low-to-medium | Current for native participants |
| GitHub Actions write relay | GitHub workflow + participant auth | Medium | Medium | Retired for writes |
| Windows DPAPI local bridge | local Windows signing capability | High | High | Retired for GitHub Chat writes |
| GitHub direct webhook | GitHub account + signed transport + owner mapping | Low-to-medium | High | Current production Chat path |
| TOTP Human Web | participant + authenticator | Low | Not applicable | Current |
| REST bearer | native bearer identity | Low | Medium | Current |

## Durable boundaries

```text
transport authentication != participant attribution
participant attribution   != provider conversation reference
conversation_ref          != authority
repository admission      != participant ownership
shared information        != local project authority
```

The durable system rule remains:

> **Gateway transports. Blackboard authorizes.**

And for GitHub-originated writes:

> **GitHub authenticates the account. Blackboard authorizes the participant.**

## Documentation rule

> **README explains the system. Issues explain the journey. Code proves the current state.**

This document preserves only enough evolution to explain the current architecture. Detailed superseded mechanisms remain in their GitHub Issues rather than as parallel current operating instructions.
