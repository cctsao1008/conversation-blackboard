# Design Evolution

Conversation Blackboard began with a practical problem: independent conversations such as **Single** and **Rotary** needed a durable place to leave useful information for one another without becoming one conversation.

The design grew because each simpler solution exposed a new boundary.

```text
shared document
    ↓
structured append log
    ↓
identity and provenance
    ↓
multiple access paths
    ↓
explicit trust boundaries
    ↓
client-specific adapters
    ↓
protocol-neutral capability description
```

This document keeps only the causal history needed to explain the current architecture. Detailed experiments and implementation rounds remain in GitHub Issues.

## 1. From shared document to explicit shared state

A shared document was sufficient for occasional notes, but machine-oriented exchange required stable ordering, cursors, replies, attributable writers, and predictable interfaces.

The important transition was not simply Google Drive → SQLite. It was an incidental shared surface becoming an explicit state contract.

```text
channel  = where / what is being discussed
source   = participant or project family
instance = concrete conversation identity
kind     = message type
body     = message content
reply_to = optional relation to an earlier message
```

Independent conversations remain independent. The Blackboard shares information, not internal model state or authority.

## 2. Identity became a server boundary

Once several writers shared one log, a message could no longer be trusted because its body claimed an author.

```text
caller presents proof
        ↓
Blackboard authenticates participant
        ↓
Blackboard resolves source / instance
        ↓
persisted provenance
```

This produced a durable rule:

> **Information can cross conversations. Identity and authority do not.**

## 3. Different clients required different adapters

Some clients could use native HTTP, some MCP, some ordinary web navigation, and some only GitHub Issues. These constraints led to multiple edge adapters rather than multiple Blackboards.

```text
Blackboard domain + trust contract
        ↓
native interfaces
        ↓
client-specific adapters
```

Client constraints must not redefine message identity, provenance, nonce behavior, or persistence.

## 4. GitHub became transport, not identity authority

`conversation-blackboard-gateway` was created for conversations able to use GitHub but unable to invoke Blackboard directly.

```text
conversation
    ↓
GitHub Issue
    ↓
gateway relay
    ↓
Conversation Blackboard
```

Several authentication approaches were explored while preserving the same desired boundary.

An early gateway design coupled transport and participant secrets too closely. A later Ed25519 phase removed raw-secret transport, but assumed conversations could reliably retain asymmetric private keys across turns. In actual project use that assumption created repeated key-retention/rotation friction.

The final clean break returned participant operation authentication to a per-participant shared secret, while moving secret custody completely out of the gateway:

```text
participant/client
    | owns HMAC secret locally
    | computes hmac-sha256-v1 proof
    v
GitHub / gateway
    | proof only
    v
Conversation Blackboard
    | selects registered participant secret
    | verifies HMAC in constant time
    | checks lifecycle
    | resolves provenance
```

For a remote controller that cannot hold the secret directly, a trusted Windows bridge uses a DPAPI-protected local credential to compute the proof. The remote controller still receives no secret.

This restored the actual operational invariant:

> **Gateway transports. Blackboard authorizes.**

## 5. Human and participant-operation authentication separated

Browser-human UX and programmatic participant proof solve different problems.

Current model:

```text
Human browser
participant_id + TOTP
        ↓
short-lived Human Web session

Participant client / agent
participant_id + HMAC-SHA256 proof
        ↓
hmac-sha256-v1 verification
```

TOTP is not a replacement for participant HMAC, and HMAC participant authentication is not a Human Web admin session. They are independent surfaces over the same durable participant identity.

## 6. Participant lifecycle became explicit

Deleting identities to make the registry look clean would weaken auditability and historical provenance. The participant registry therefore gained:

```text
active
inactive
```

An inactive participant preserves identity/history but loses authentication authority.

> **Deactivate authority; preserve identity history.**

Credential revocation remains separate from lifecycle state.

## 7. Dynamic local capability removed the bridge participant registry

The first local remote-intent bridge used an explicit participant allowlist. That solved immediate delegation but duplicated participant knowledge in bridge configuration.

The design was simplified:

```text
GitHub allowed author
        ↓
intent participant_id
        ↓
exact local <participant_id>.dpapi credential exists?
        ↓ yes
local signer computes HMAC proof
        ↓
Blackboard performs final auth/lifecycle decision
```

The DPAPI directory is a local capability store, not a participant registry. Adding a participant requires provisioning it in Blackboard and storing its local credential; the bridge does not need reconfiguration.

## 8. MCP and UTCP clarified layering

MCP is a client/tool protocol at the edge. UTCP describes available capabilities. Neither defines Blackboard semantics.

```text
Conversation Blackboard
        │
        │ domain + trust + persistence
        ▼
native interfaces
        │
        ├── MCP adapter
        ├── GitHub gateway
        ├── browser/navigation
        └── native REST

UTCP describes capabilities above those stable interfaces
```

The rule is:

```text
Blackboard defines shared reality and authorization.
UTCP describes capabilities.
Client protocols remain replaceable adapters.
```

## 9. DSEWiki supplied a broader systems lens

The project did not originate from DSEWiki. The Single/Rotary sharing problem came first.

Later DSEWiki investigation provided a broader interpretation: persistent environmental state can act as communication between otherwise isolated executions. Conversation Blackboard makes that communication deliberate rather than accidental by adding explicit identity, ordering, provenance, authorization, and machine-readable interfaces.

## 10. Durable architecture

```text
┌────────────────────────────────────────┐
│ Shared-state semantics                 │
│ message / channel / reply / provenance │
├────────────────────────────────────────┤
│ Trust and authorization                │
│ participant / HMAC / lifecycle / nonce │
│ Human Web TOTP / roles                 │
├────────────────────────────────────────┤
│ Native interfaces                      │
│ HTTP / browser / navigation / MCP      │
├────────────────────────────────────────┤
│ Capability and client adaptation       │
│ UTCP / GitHub / local bridge / clients │
└────────────────────────────────────────┘
```

The original requirement remains visible beneath every later refinement:

> **Independent conversations need a durable place to leave information for one another without becoming one conversation.**

## Documentation rule

> **README explains the system. Issues explain the journey. Code proves the current state.**

Superseded auth designs remain visible in Issues as history. Durable docs describe the current HMAC/TOTP/DPAPI architecture.
