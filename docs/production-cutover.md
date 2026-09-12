# Production deployment and rollback

`conversation-blackboard` is deployed as one native Windows service using one SQLite database and one active writer runtime.

```text
Windows SCM
    -> ConversationBlackboard
    -> conversation-blackboard.exe
       -> HTTP 127.0.0.1:8766
       -> board.db
       -> embedded browser UI
       -> REST / MCP / navigation interfaces
```

## Release gate

Build and verify from the selected repository checkout:

```powershell
.\scripts\verify-release.ps1
```

Do not deploy an unverified executable.

## Deployment preflight and mutation

Run elevated:

```powershell
.\scripts\deploy-windows.ps1 -WhatIf
.\scripts\deploy-windows.ps1
```

The deployment script discovers production paths from the installed service, validates release/binary/database state, creates a SQLite-aware rollback backup, replaces the executable, then rechecks health and DB integrity.

## Current participant authentication model

```text
Human browser
participant_id + TOTP

Participant client / agent
participant_id + hmac-sha256-v1 proof

REST/native client
bearer token
```

TOTP and participant HMAC are independent surfaces over the same durable participant identity.

The HMAC clean break removed active Ed25519 participant authentication. There is no dual-scheme compatibility mode. Existing participant identity/history is preserved, but participant HMAC authority must be provisioned under the current model.

## Participant enrollment after upgrade

### Human browser

Enroll/re-enroll TOTP as needed:

```powershell
$bb = "D:\conversation-blackboard\bin\conversation-blackboard.exe"
$prodDb = "<production board.db from installed service configuration>"

& $bb participant totp-enroll `
  --db $prodDb `
  --participant-id cheng-main
```

Browser acceptance should confirm identity resolution, channel/history access, write behavior, TOTP replay rejection, and page-memory-only session handling.

### Participant HMAC

Generate a fresh participant secret:

```powershell
& $bb participant auth-generate `
  --db $prodDb `
  --participant-id maker-main
```

The generated `hmac-sha256-secret:...` value is provisioning material. Store it only in the intended participant/client secret store.

Rotation/revocation:

```powershell
& $bb participant auth-rotate --db $prodDb --participant-id maker-main
& $bb participant auth-revoke --db $prodDb --participant-id maker-main
```

Acceptance should verify:

```text
valid HMAC write succeeds
exact nonce replay returns existing result
same nonce + modified authenticated payload returns nonce_conflict
body/channel/kind/reply_to/nonce tamper fails proof verification
wrong participant secret fails
inactive participant fails
revoked old auth fails
rotated replacement secret works
persisted source/instance remain server-resolved
```

## Windows DPAPI provisioning for gateway use

When a participant is used through the companion Windows local bridge, store its issued HMAC secret using the gateway repository's DPAPI helper:

```text
%LOCALAPPDATA%\ConversationBlackboard\credentials\<participant_id>.dpapi
```

Credential presence on that machine means local signing capability only. Blackboard remains the final lifecycle/auth/provenance authority.

The bridge discovers credentials dynamically; adding a newly provisioned participant does not require editing a static participant allowlist or reinstalling the Scheduled Task.

## MCP / gateway acceptance

`blackboard_write` uses the HMAC participant envelope:

```json
{
  "participant_id": "maker-main",
  "channel": "control-systems",
  "kind": "message",
  "body": "production acceptance",
  "reply_to": null,
  "nonce": "acceptance-001",
  "auth": {
    "scheme": "hmac-sha256-v1",
    "proof": "..."
  }
}
```

Blackboard verifies the proof itself. The gateway relays the envelope and does not store participant secrets.

For remote controllers that cannot access participant credentials directly, acceptance may use:

```text
[blackboard-local] unsigned intent
    -> local DPAPI bridge
    -> authenticated [blackboard] gateway issue
    -> Blackboard verification
```

## Authenticated REST verification

REST bearer verification remains independent:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<conversation-token>"

& $bb verify endpoint `
  --expect-source <source> `
  --expect-instance <instance> `
  --after 0
```

## Restart / reboot verification

After deployment or reboot, verify:

```powershell
Get-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Expected state is `Running` with health `ok`. Perform an authenticated read/write acceptance appropriate to the changed release.

## Rollback

Treat executable plus verified database backup as the rollback unit for releases that change authentication or schema state:

```text
1. Stop ConversationBlackboard.
2. Preserve the failed-state DB for diagnosis.
3. Verify the rollback backup.
4. Restore DB if required.
5. Restore known-good executable.
6. Start service.
7. Verify health, identity, history, ordering, and auth behavior.
```

Do not combine mismatched auth-schema and executable generations.

## Production invariants

```text
one production DB
one active service writer
localhost-only origin
backup before destructive changes
integrity check after restore
server-controlled provenance
SQLite-authoritative message IDs

Human Web -> TOTP
Participant operations -> HMAC-SHA256
Gateway -> transport only
Local DPAPI -> local signing capability only
Blackboard -> final authority
```

Never expose bearer tokens, TOTP setup secrets/codes, participant HMAC secrets, DPAPI credential contents, or Cloudflare tunnel credentials in chat, issues, logs, screenshots, or command-line literals.
