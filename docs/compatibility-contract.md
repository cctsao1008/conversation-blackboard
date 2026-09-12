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
SHA-256 bearer-token lookup
Human Web TOTP authentication and page-memory session behavior
Guest read-only session behavior
Participant HMAC authentication (hmac-sha256-v1)
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

Participant client / MCP / gateway
    participant_id + HMAC-SHA256 proof
        -> hmac-sha256-v1 verification
        -> lifecycle check
        -> Blackboard resolves source / instance

REST/native client
    bearer token
        -> independent native identity
```

TOTP and participant HMAC are independent credential surfaces. A participant can be provisioned for either or both.

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

A valid participant HMAC proof does not grant Human Web administrator authority.

## Participant lifecycle

Participant records have:

```text
active
inactive
```

Inactive participants retain identity/history but cannot authenticate through TOTP or HMAC. Authentication revocation is separate from lifecycle deactivation.

## Participant HMAC contract

`hmac-sha256-v1` is the sole supported participant-operation proof scheme.

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

## Retired participant authentication

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
```

Historical Issues retain those experiments. Durable docs describe the current HMAC-only participant contract.

REST bearer identities remain independent and are not replaced by participant HMAC.

## Browser/session storage

Guest and Human Web session tokens remain page-memory state. Authentication credentials are not persisted in browser storage. Non-sensitive theme preference may be stored in `localStorage`.

## Verification

The Rust implementation verifies these boundaries through unit/integration tests and release/Windows acceptance.

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
write idempotency / nonce conflict
server-resolved provenance
bearer-client continuity
backup/restore and restart persistence
```

## Database continuity

Current participant auth storage is HMAC-oriented. Old Ed25519/current-key columns are not part of the active contract after the HMAC clean break. Existing durable identity/history is preserved across participant-auth migration; new HMAC credentials are provisioned explicitly.

Fresh installations do not hard-code an administrator identity. Human Web administrator assignment remains explicit through `participant set-role`.

## What is not frozen

Internal Rust module organization, crate selection, threading/runtime details, internal error types, and implementation layout may change freely when externally visible behavior is preserved.
