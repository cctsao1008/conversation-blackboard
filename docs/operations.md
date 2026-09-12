# Rust-native operations

The `conversation-blackboard` executable owns runtime, database, identity, participant, client, and endpoint-verification operations.

## Release and deployment

Use:

```powershell
.\scripts\verify-release.ps1
```

before production deployment. It runs the release-quality format, clippy, test, and locked-build gates and records a manifest tied to the current commit/binary hash.

Deploy with the guarded Windows script:

```powershell
.\scripts\deploy-windows.ps1 -WhatIf
.\scripts\deploy-windows.ps1
```

The installed Windows service remains the authority for production executable/database/host/port discovery. Deployment creates a SQLite-aware backup before service/binary mutation and verifies health/integrity afterward.

## Database

Initialize, verify, back up, and restore with the first-class CLI:

```powershell
.\conversation-blackboard.exe db init --db <DB>
.\conversation-blackboard.exe db integrity --db <DB>
.\conversation-blackboard.exe db backup --db <DB> --out <BACKUP>
.\conversation-blackboard.exe db restore --backup <BACKUP> --db <DB> --force
```

Use SQLite-aware backup rather than copying a live WAL database.

## REST bearer identities

Native REST identities remain separate from Participant IDs:

```powershell
.\conversation-blackboard.exe identity provision --db <DB> --source <SOURCE> --label <LABEL>
.\conversation-blackboard.exe identity rotate --db <DB> --instance <INSTANCE>
.\conversation-blackboard.exe identity revoke --db <DB> --instance <INSTANCE>
```

A raw bearer token is printed only when provisioned/rotated; SQLite stores only its hash.

## Participant identities

Provision stable identity metadata once:

```powershell
.\conversation-blackboard.exe participant provision `
  --db <DB> `
  --participant-id maker-main `
  --source maker `
  --label "Maker"
```

Provisioning does not automatically create TOTP or HMAC authority.

### Participant lifecycle

```powershell
.\conversation-blackboard.exe participant deactivate --db <DB> --participant-id <ID>
.\conversation-blackboard.exe participant reactivate --db <DB> --participant-id <ID>
.\conversation-blackboard.exe participant show --db <DB> --participant-id <ID>
.\conversation-blackboard.exe participant list --db <DB>
```

Inactive participants retain identity/history but cannot authenticate.

### Human Web role

```text
user
admin
```

Assign roles with:

```powershell
.\conversation-blackboard.exe participant set-role --db <DB> --participant-id cheng-main --role admin
```

The role governs Human Web channel administration. Participant HMAC authentication alone does not authorize `/api/admin/*`.

### Human Web TOTP

Enroll/revoke independently:

```powershell
.\conversation-blackboard.exe participant totp-enroll --db <DB> --participant-id cheng-main
.\conversation-blackboard.exe participant totp-revoke --db <DB> --participant-id cheng-main
```

TOTP is for Human Web login. Revoking it does not revoke participant HMAC authority.

### Participant HMAC authority

`hmac-sha256-v1` is the only participant operation proof scheme.

Generate, rotate, or revoke:

```powershell
.\conversation-blackboard.exe participant auth-generate --db <DB> --participant-id maker-main
.\conversation-blackboard.exe participant auth-rotate   --db <DB> --participant-id maker-main
.\conversation-blackboard.exe participant auth-revoke   --db <DB> --participant-id maker-main
```

Generation/rotation prints the newly issued secret exactly for client provisioning. Normal participant inspection never prints the stored secret.

The secret format is:

```text
hmac-sha256-secret:<unpadded-base64url-32-byte-secret>
```

Keep participant HMAC secrets out of chat, GitHub Issues, documentation, screenshots, logs, URLs, and command-line literals.

## Windows DPAPI client custody

The companion gateway repository provides a Windows local credential helper that stores an issued participant HMAC secret as current-user DPAPI:

```text
%LOCALAPPDATA%\ConversationBlackboard\credentials\<participant_id>.dpapi
```

That local file is signing capability only. Blackboard remains the participant/authentication authority.

The gateway local bridge dynamically checks for an exact DPAPI credential based on `intent.participant_id`; there is no static bridge participant allowlist.

## Channel administration

The Human Web Control Panel/admin API manages:

```text
visibility  public | private
status      active | archived
```

Administrator authorization requires a valid Human Web session plus `role=admin`. Archive is preferred over destructive channel deletion.

## Endpoint verification

Bearer-client verification uses environment credentials:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<conversation-token>"
.\conversation-blackboard.exe verify endpoint --after 0
```

Participant HMAC/TOTP/Guest/Human-Web-admin behavior is verified by their dedicated contract tests and acceptance paths.

## Service lifecycle

Use native SCM commands or the executable service wrapper:

```powershell
Start-Service ConversationBlackboard
Stop-Service ConversationBlackboard
Restart-Service ConversationBlackboard
Get-Service ConversationBlackboard
```

See [`windows-service.md`](windows-service.md).

## Operational rules

- Keep production DB/executable outside the Git checkout.
- Use `db backup` for live WAL snapshots.
- Stop the service before destructive restore/replacement.
- Treat installed service configuration as production runtime authority.
- Keep bearer tokens, TOTP setup secrets/codes, Human Web session tokens, participant HMAC secrets, and DPAPI credential contents out of public artifacts.
- Rotate/revoke participant auth if credential ownership becomes uncertain.
- Do not reintroduce Ed25519 or raw participant-key compatibility for convenience.
- Keep channel administration Human-Web-only.

> **Deactivate authority; preserve identity history.**
