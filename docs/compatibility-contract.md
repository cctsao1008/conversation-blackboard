# Runtime compatibility contract

`compat/contract.json` records externally visible behavior that implementation changes should preserve unless the product contract is deliberately versioned.

## Current frozen boundaries

```text
HTTP routes and status codes
JSON response shapes
request-size and page-size limits
message ordering and cursor semantics
channel visibility / status semantics
reply_to behavior
server-resolved source / instance provenance
optional message conversation_ref provenance
SHA-256 bearer-token lookup
Human Web TOTP authentication and page-memory session behavior
Guest read-only session behavior
Participant HMAC authentication (hmac-sha256-v1)
GitHub participant owner mapping
GitHub signed-webhook write contract
Human-Web-only administrator authority
participant active/inactive lifecycle
SQLite messages / identities / web_participants / channels schema
WAL operation
browser security headers and storage boundaries
```

The schema fingerprint is recorded in `compat/contract.json`. A deliberate schema/API change should update the machine contract explicitly rather than drift through documentation alone.

## Access and proof model

```text
Guest
    -> short-lived guest session
    -> public + active channels only
    -> read only

Human Web
    participant_id + RFC 6238 TOTP
        -> short-lived Human Web session
        -> user/admin role

Native participant client / MCP
    participant_id + HMAC-SHA256 proof
        -> hmac-sha256-v1 verification
        -> lifecycle check
        -> Blackboard resolves source / instance

REST/native client
    bearer token
        -> independent native identity

Remote Chat through GitHub
    authenticated GitHub Issue author
        + signed webhook
        + participant owner mapping
        -> lifecycle check
        -> Blackboard resolves source / instance
        -> optional conversation_ref provenance
```

TOTP, participant HMAC, bearer identity, and GitHub ownership are distinct authority surfaces. They may refer to related durable identities but are not interchangeable credentials.

For GitHub-originated writes:

```text
GitHub user ID      = authentication principal
participant_id      = logical Blackboard attribution identity
signed webhook      = authenticated transport
conversation_ref    = optional provenance only
Blackboard          = final authorization authority
```

## Channel visibility and lifecycle

Channels have:

```text
visibility = public | private
status     = active | archived
```

New channels default to `private + active`. Guests see only public active channels. Authenticated participants may read public/private channels; archived channels reject new writes until reactivated.

Channel administration requires:

```text
valid Human Web session
participant role == admin
```

Neither a valid participant HMAC proof nor GitHub owner authorization grants Human Web administrator authority.

## Participant lifecycle

Participant records have:

```text
active
inactive
```

Inactive participants retain identity/history but cannot authenticate through TOTP/HMAC and cannot receive delegated GitHub writes. Authentication revocation and external-owner binding are separate from lifecycle deactivation.

## Participant HMAC contract

`hmac-sha256-v1` is the sole supported participant-operation proof scheme for direct/native participant clients.

A participant secret is 256 random bits represented as:

```text
hmac-sha256-secret:<unpadded-base64url-secret>
```

The write proof binds:

```text
auth_version
participant_id
channel
kind
body
reply_to
nonce
```

The authenticated private-read proof binds:

```text
auth_version
purpose = blackboard-read-v1
participant_id
channel
after
limit
```

Proofs are HMAC-SHA256 and are verified in constant time. Exact write retry remains idempotent; nonce reuse with a changed authenticated payload is rejected.

## GitHub webhook contract

GitHub Chat writes do not carry participant HMAC proof. The active `[blackboard]` Issue contract is authenticated by GitHub account identity plus signed webhook transport, then authorized by Blackboard participant ownership.

The participant owner relation uses:

```text
owner_provider = github
owner_subject  = stable GitHub numeric user ID
owner_login    = display metadata only
```

The active optional provenance field is:

```text
conversation_ref
```

It is not required to be an RFC UUID, is not globally unique, and has no authentication or authorization effect.

The former `conversation_uuid` name is not an active payload alias; it exists only in migration compatibility logic for development databases created during the earlier implementation round.

Webhook retries use a deterministic operation identity derived from repository ID and Issue number. A retry with the same normalized payload returns the existing result; a changed normalized payload conflicts.

## Retired authentication / transport paths

These are not active compatibility surfaces:

```text
raw participant private-key transport
bbcred-v1 browser credential bundles
X-Blackboard-Private-Key
/w/... ?key=<private-key>
MCP private_key participant writes
Ed25519 participant signing
participant public-key registration
ed25519-v1 envelopes
/api/auth/challenge
/api/auth/verify
GitHub Actions authenticated-write relay
[blackboard-local] Windows polling bridge
DPAPI-backed GitHub Chat signer
```

Historical Issues retain those experiments. The Windows DPAPI/local bridge is not a compatibility path for current GitHub Chat writes.

REST bearer identities and native participant HMAC remain current independent interfaces.

## Browser/session storage

Guest and Human Web session tokens remain page-memory state. Authentication credentials are not persisted in browser storage. Non-sensitive theme preference may be stored in `localStorage`.

## Verification

The Rust implementation verifies these boundaries through unit/integration tests and release/Windows acceptance. Machine-readable message schemas exposed through MCP, OpenAPI, and UTCP all include nullable `conversation_ref` provenance.

Important scenarios include:

```text
TOTP vectors, skew, replay, throttling
Guest public-read and private/write denial
Human-Web-only administration
channel migration and archive write denial
HMAC valid/wrong-secret/tamper rejection
private authenticated reads
participant auth rotation/revocation
inactive participant denial
GitHub valid/wrong-owner/inactive participant handling
GitHub signature/repository/author admission checks
conversation_ref validation + persistence
GitHub retry idempotency / nonce conflict
server-resolved provenance
bearer-client continuity
backup/restore and restart persistence
```

## Database continuity

Current participant auth storage is HMAC-oriented, with optional GitHub external-owner metadata. Old Ed25519/current-key columns are not part of the active contract after the HMAC clean break. Existing durable identity/history is preserved across participant-auth migration.

`messages.conversation_ref` is nullable. Historical rows remain valid with `NULL` provenance.

Fresh installations do not hard-code an administrator identity. Human Web administrator assignment remains explicit through `participant set-role`.

## What is not frozen

Internal Rust module organization, crate selection, threading/runtime details, internal error types, and implementation layout may change freely when externally visible behavior is preserved.
