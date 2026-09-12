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

Provisioning does not automatically create TOTP, HMAC, or external-owner authority.

A `participant_id` is a durable logical attribution identity. It does not have to correspond one-to-one with one physical Chat.

### Participant lifecycle

```powershell
.\conversation-blackboard.exe participant deactivate --db <DB> --participant-id <ID>
.\conversation-blackboard.exe participant reactivate --db <DB> --participant-id <ID>
.\conversation-blackboard.exe participant show --db <DB> --participant-id <ID>
.\conversation-blackboard.exe participant list --db <DB>
```

Inactive participants retain identity/history but cannot authenticate through TOTP/HMAC and cannot receive delegated GitHub writes.

### Human Web role

```text
user
admin
```

Assign roles with:

```powershell
.\conversation-blackboard.exe participant set-role --db <DB> --participant-id cheng-main --role admin
```

The role governs Human Web channel administration. Participant HMAC authentication or GitHub owner authorization alone does not authorize `/api/admin/*`.

### Human Web TOTP

Enroll/revoke independently:

```powershell
.\conversation-blackboard.exe participant totp-enroll --db <DB> --participant-id cheng-main
.\conversation-blackboard.exe participant totp-revoke --db <DB> --participant-id cheng-main
```

TOTP is for Human Web login. Revoking it does not revoke participant HMAC authority or an external GitHub owner relation.

### Participant HMAC authority

`hmac-sha256-v1` is the native participant operation proof scheme.

Generate, rotate, or revoke:

```powershell
.\conversation-blackboard.exe participant auth-generate --db <DB> --participant-id maker-main
.\conversation-blackboard.exe participant auth-rotate   --db <DB> --participant-id maker-main
.\conversation-blackboard.exe participant auth-revoke   --db <DB> --participant-id maker-main
```

Generation/rotation prints the newly issued secret exactly for direct client provisioning. Normal participant inspection never prints the stored secret.

The secret format is:

```text
hmac-sha256-secret:<unpadded-base64url-32-byte-secret>
```

Keep participant HMAC secrets out of chat, GitHub Issues, documentation, screenshots, logs, URLs, and command-line literals.

## GitHub participant ownership

GitHub-originated Chat writes use an external owner relation rather than participant HMAC.

Assign a GitHub owner with the stable numeric user ID:

```powershell
.\conversation-blackboard.exe participant set-owner `
  --db <DB> `
  --participant-id maker-main `
  --provider github `
  --subject <stable-github-numeric-id> `
  --login <display-login>
```

Inspect with `participant show` or `participant list`. The login is display metadata; `owner_subject` is the authorization key.

Clear only the external owner relation with:

```powershell
.\conversation-blackboard.exe participant clear-owner `
  --db <DB> `
  --participant-id maker-main
```

Clearing the GitHub owner does not revoke TOTP/HMAC credentials or delete history.

For GitHub-originated writes:

```text
GitHub user ID      = authentication principal
participant_id      = logical Blackboard attribution identity
signed webhook      = authenticated transport
conversation_ref    = optional provider-side provenance only
Blackboard          = final authorization authority
```

See [`github-integration.md`](github-integration.md).

## GitHub webhook runtime configuration

The GitHub write endpoint is disabled unless both environment values are available to the Blackboard service process:

```text
BLACKBOARD_GITHUB_REPOSITORY_ID
BLACKBOARD_GITHUB_WEBHOOK_SECRET
```

The webhook secret is operator-managed transport authentication material between GitHub and Blackboard. Never store it in Issues, documentation, screenshots, source code, or command-line literals.

For a Windows SCM service, configure environment values so the service process receives them at startup, then restart the service and verify `/api/health` plus the webhook acceptance path.

The active endpoint is:

```text
POST /integrations/github/issues
```

The retired Windows DPAPI/local bridge is not a current GitHub Chat write path.

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

Participant HMAC/TOTP/Guest/Human-Web-admin/GitHub-webhook behavior is verified by their dedicated contract tests and acceptance paths.

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
- Keep bearer tokens, TOTP setup secrets/codes, Human Web session tokens, participant HMAC secrets, and GitHub webhook secrets out of public artifacts.
- Rotate/revoke participant auth if direct-client credential ownership becomes uncertain.
- Clear/rebind GitHub ownership if account attribution changes.
- Do not reintroduce Ed25519, GitHub Actions write relay, or the DPAPI local bridge as compatibility paths.
- Keep channel administration Human-Web-only.

> **Deactivate authority; preserve identity history.**
