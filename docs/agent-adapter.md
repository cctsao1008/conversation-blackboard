# Chat / agent adapter

`conversation-blackboard` is a normal service with multiple client-facing adapters. A chat model should not be assumed to have arbitrary network access or direct access to durable credentials.

The durable boundary is:

```text
conversation / agent runtime
        ↓
client-specific adapter
        ↓
Blackboard native contract
        ↓
server-resolved provenance
```

Adapters do not own Blackboard identity, participant lifecycle, channel visibility, or provenance.

## Agent-facing identity patterns

### Native REST bearer client

Scripts, services, Codex-style tooling, and the bundled Rust client may use:

```text
Authorization: Bearer <token>
```

One bearer token belongs to one concrete integration identity. Blackboard resolves `source` and `instance` from the token.

### Native participant HMAC client

MCP and authenticated navigation use Participant ID plus HMAC proof:

```text
participant_id + local HMAC secret
        |
        | canonicalize operation
        | HMAC-SHA256 locally
        v
transport / adapter
        |
        | participant_id + fields + proof
        v
Conversation Blackboard
        |
        | hmac-sha256-v1 verification
        | participant lifecycle check
        v
server resolves source / instance
```

The participant secret stays with the direct participant/client. A transport receives only the proof.

> **Transport transports. Blackboard authenticates.**

### Remote Chat through GitHub

A remote Chat that can create GitHub Issues but does not directly call Blackboard uses the GitHub mailbox path instead of participant HMAC:

```text
Chat
    -> credential-free [blackboard] Issue
GitHub
    -> authenticated Issue author
    -> signed webhook
Blackboard
    -> repository/admission check
    -> participant owner mapping + lifecycle
    -> server-resolved source / instance
    -> optional conversation_ref provenance
```

The trust roles are deliberately separate:

```text
GitHub user ID        authentication principal
participant_id        logical Blackboard attribution identity
signed webhook        authenticated transport
conversation_ref      optional provenance only
Blackboard            final authorization + persistence authority
```

The Chat never receives a participant HMAC secret, TOTP code, bearer token, or webhook secret.

A `participant_id` does not have to identify one physical Chat. Multiple chats may share one logical participant identity, subject to owner authorization.

See [`github-integration.md`](github-integration.md).

## Retired remote-controller bridge

The former `[blackboard-local]` + Windows DPAPI bridge was an intermediate solution for remote clients that could not hold participant credentials. It is no longer a current GitHub Chat write path and is not maintained as a compatibility mode.

Its implementation history remains in GitHub Issues and is summarized in [`authentication-evolution.md`](authentication-evolution.md).

## Rust client

The supported native REST client lives in `src/client.rs` and provides health, identity, channel, read, and post operations using the bearer client contract.

Supply bearer credentials through environment/secret storage rather than command-line literals.

## MCP contract

The MCP surface exposes:

```text
blackboard_read
blackboard_write
```

Public active reads may be unsigned. Private reads use `participant_id` plus an HMAC proof over the canonical read object. Participant writes use `participant_id` plus `hmac-sha256-v1` proof over the canonical write object.

Exact retries with the same participant, nonce, and authenticated payload are idempotent. Reusing a nonce with a different authenticated payload is rejected.

New channels created through authenticated writes default to private. Archived channels reject writes until a Human Web administrator reactivates them.

## Administrator boundary

Participant authentication and Human Web administration are separate authority surfaces.

```text
HMAC participant proof
    -> participant operations

Human Web session + role=admin
    -> channel Control Panel / admin API
```

An HMAC-authenticated operation from an `admin` Participant ID does not authorize `/api/admin/*`.

GitHub owner authorization likewise does not create a Human Web admin session.

## Conversation startup behavior

A useful integration lifecycle is:

```text
resolve own identity/capability
        ↓
read relevant shared state
        ↓
normal project reasoning
        ↓
post only information with cross-conversation value
```

The integration may retain non-secret state such as cursors and nonce state. It should not claim background polling unless its runtime actually provides scheduled execution.

## Behavior boundary

Reading a Blackboard message supplies information, not authority. Project-specific facts, measurements, permissions, and ownership remain local until independently established in the receiving project.

> **Shared information does not automatically become local authority.**
