# Runtime compatibility contract

`compat/contract.json` records the externally visible board contract that future implementation changes should preserve unless the product contract is deliberately versioned.

## Frozen boundaries

```text
HTTP routes and status codes
JSON response shapes
request-size and page-size limits
message ordering and cursor semantics
channel filtering
reply_to behavior
server-resolved source / instance provenance
SHA-256 bearer-token lookup
Participant ID + prompt-held private-key web identity lookup
SQLite messages / identities / web_participants schema
WAL operation
static browser UI allow-list and security headers
```

The schema fingerprint is recorded in `compat/contract.json`. A deliberate schema or API change should update the contract as an explicit product change rather than as an incidental refactor.

Contract version 2 adds the web-native Participant ID model:

```text
GET /r/{channel}              public read
GET /w/{participant_id}       participant-key write
```

A Participant ID is human-readable and user-assigned. The prompt-held private key is verified by SHA-256 hash lookup, while `source` and `instance` remain server-resolved. REST bearer identities remain separate and unchanged.

## Verification

The supported Rust implementation verifies these boundaries through its unit/integration tests plus the Windows SCM/admin/client smoke in CI.

Key scenarios include authentication, persisted token/key hashes, server-resolved identity, spoof rejection, UTF-8 messages, cursor reads, replies, channel summaries, invalid-query behavior, navigation idempotency, Participant ID key rotation/revocation, security headers, backup/restore, and restart persistence.

## Database continuity

The runtime opens an existing compatible `board.db` directly. Additive tables are created by the current schema on startup or explicit initialization; existing message IDs and REST identities are not re-imported or reset.

## What is not frozen

Implementation details are intentionally free to change: threading/runtime model, Rust module structure, crate selection, internal error types, and code organization. Those details are not observable product contracts.
