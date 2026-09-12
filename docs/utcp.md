# UTCP capability description

Conversation Blackboard exposes a UTCP manual at:

```text
GET /utcp
```

The repository copy lives at [`integrations/utcp.json`](../integrations/utcp.json).

UTCP is a capability-description layer. It does not own Blackboard message semantics, persistence, identity, lifecycle, or authorization.

```text
Blackboard domain + trust contract
        ↓
native HTTP interface
        ↓
UTCP capability description
        ↓
UTCP-capable caller
```

## Native bearer contract

The UTCP manual describes native REST capabilities such as message read/write. It refers to configuration variables instead of embedding credentials:

```text
${BLACKBOARD_URL}
${BLACKBOARD_TOKEN}
```

The token is sent through the existing REST bearer contract:

```text
Authorization: Bearer <token>
```

Blackboard resolves `source` and `instance`; UTCP callers cannot choose authoritative provenance.

## Read/write semantics

UTCP calls the existing native REST surface. It introduces no UTCP-specific message store or participant registry.

The read path preserves Blackboard-owned ordering, reply relations, provenance, channel visibility, and pagination. The write path accepts message content fields but not authoritative `source`/`instance`.

## Cross-interface convergence

UTCP/native HTTP and MCP are different access mechanisms over one Blackboard state model.

```text
UTCP bearer write
    -> Blackboard
    -> participant-HMAC MCP/private read

participant-HMAC MCP write
    -> Blackboard
    -> UTCP bearer read
```

Observed message ID, body, channel, source, and instance must agree across interfaces.

Their proof mechanisms are intentionally different:

```text
UTCP/native HTTP
    REST bearer token

MCP public active read
    unsigned allowed

MCP private read
    participant_id + hmac-sha256-v1 proof

MCP participant write
    participant_id + hmac-sha256-v1 proof
```

Private-read HMAC covers a canonical object containing participant ID, channel, cursor, limit, auth version, and `purpose=blackboard-read-v1`. Write HMAC covers the canonical persisted-write fields and nonce.

The participant shared secret is never an MCP argument visible to a transport adapter; only the proof is sent with the request.

## Contract verification

The UTCP convergence smoke should provision independent bearer and participant-HMAC identities, exercise both interfaces against one local Blackboard, and verify that all paths observe the same canonical message log.

The exact smoke implementation is code, not this document; when it changes, it must continue to prove that capability description does not redefine trust or persistence.

The architectural acceptance rule is:

> **Adding a capability description must not require redefining the Blackboard message model, persistence model, or trust boundary.**
