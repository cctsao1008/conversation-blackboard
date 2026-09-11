# Rust-native operations

The `conversation-blackboard` executable owns runtime, database, identity, participant, client, and endpoint-verification operations.

## Local release verification

Use the repository script instead of manually repeating the release gate:

```powershell
.\scripts\verify-release.ps1
```

The script derives the repository root from `$PSScriptRoot`, verifies that the checkout is the `conversation-blackboard` Cargo package, requires a clean Git worktree, and runs the same release-quality gates used by CI:

```text
cargo fmt --all -- --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-targets
cargo build --release --locked
```

After a successful build it verifies that the executable can start its CLI, records the current Git commit and SHA-256, and writes an ignored release manifest under `target/release/`.

The Windows deployment script requires that manifest and refuses to deploy if the current `HEAD` or binary hash no longer matches it.

The verification script deliberately does **not** run `git pull`. It validates the checkout the operator explicitly selected.

## Guarded Windows production deployment

Run from an elevated PowerShell after release verification:

```powershell
.\scripts\deploy-windows.ps1
```

Production filesystem paths are not hard-coded in the script. The repository/build path is derived from the script location. The installed Windows service is the runtime authority for the production executable, database path, host, and port.

Use standard PowerShell `-WhatIf` for a read-only deployment preflight:

```powershell
.\scripts\deploy-windows.ps1 -WhatIf
```

The deployment preflight validates the release manifest and binary hash, installed service configuration, current service health, and production database integrity before mutation.

The mutation phase:

```text
create and verify SQLite-aware DB backup
preserve current executable
stop service and wait for Stopped
replace executable
verify installed SHA-256
start service and wait for Running
verify /api/health
verify production DB integrity again
```

If replacement or post-replacement startup/health verification fails, the script attempts to restore the preserved executable and return the service to `Running`.

## Database

Initialize a new database:

```powershell
.\conversation-blackboard.exe db init `
  --db D:\conversation-blackboard-runtime\board.db
```

Check integrity:

```powershell
.\conversation-blackboard.exe db integrity `
  --db D:\conversation-blackboard-runtime\board.db
```

Create a consistent backup while the service is running:

```powershell
.\conversation-blackboard.exe db backup `
  --db D:\conversation-blackboard-runtime\board.db `
  --out D:\conversation-blackboard-backups\board-backup.db
```

Prefer this SQLite-aware backup over copying a live WAL database with ordinary filesystem copy commands.

Restore only while the service is stopped:

```powershell
Stop-Service ConversationBlackboard

.\conversation-blackboard.exe db restore `
  --backup D:\conversation-blackboard-backups\board-backup.db `
  --db D:\conversation-blackboard-runtime\board.db `
  --force
```

Restore validates both the input backup and reconstructed database.

### Access-control migration

Database initialization is also the additive migration entry point for the access-control plane.

For an existing compatible database it preserves messages, global IDs, bearer identities, Participant IDs, TOTP state, and registered public keys while adding:

```text
web_participants.role
channels metadata table
```

Existing message channels are materialized into the `channels` table. `blackboard-lounge` becomes `public + active`; other existing channels become `private + active`.

When the `role` column is first added to an existing production-style database, an existing `cheng-main` participant is promoted to `admin`. Fresh databases do not hard-code an administrator during later participant provisioning; use `participant set-role` explicitly.

## REST bearer identities

Provision a REST identity:

```powershell
.\conversation-blackboard.exe identity provision `
  --db D:\conversation-blackboard-runtime\board.db `
  --source control-project `
  --label "Controller architecture conversation"
```

A raw bearer token is printed once. Only its SHA-256 hash is stored in SQLite.

Rotate:

```powershell
.\conversation-blackboard.exe identity rotate `
  --db D:\conversation-blackboard-runtime\board.db `
  --instance <instance>
```

Revoke:

```powershell
.\conversation-blackboard.exe identity revoke `
  --db D:\conversation-blackboard-runtime\board.db `
  --instance <instance>
```

REST bearer identity is independent from Participant ID authentication.

## Participant identities

A Participant ID is the shared identity selector used by human TOTP login and/or agent Ed25519 signing.

Provision it once:

```powershell
.\conversation-blackboard.exe participant provision `
  --db D:\conversation-blackboard-runtime\board.db `
  --participant-id cheng-main `
  --source human `
  --label "Cheng"
```

Provisioning creates identity metadata with role `user`. It does not create a human or agent credential automatically.

### Human Web role

Roles are explicit:

```text
user
admin
```

Assign administrator role:

```powershell
.\conversation-blackboard.exe participant set-role `
  --db D:\conversation-blackboard-runtime\board.db `
  --participant-id cheng-main `
  --role admin
```

Return a participant to ordinary Human Web authority with `--role user`.

The role affects the Human Web channel-control plane only. An Ed25519 agent signature from an `admin` Participant ID does **not** authorize `/api/admin/*` routes.

### Human TOTP enrollment

Enroll:

```powershell
.\conversation-blackboard.exe participant totp-enroll `
  --db D:\conversation-blackboard-runtime\board.db `
  --participant-id cheng-main
```

The command prints:

```text
setup_key
otpauth_uri
```

Add the account to Google Authenticator or another RFC 6238-compatible authenticator.

Revoke human web login:

```powershell
.\conversation-blackboard.exe participant totp-revoke `
  --db D:\conversation-blackboard-runtime\board.db `
  --participant-id cheng-main
```

This does not affect the participant's Ed25519 signing key or role.

### Agent Ed25519 signing key

Generate a keypair locally:

```powershell
.\conversation-blackboard.exe participant generate-signing-key
```

The command prints:

```text
signature_scheme
public_key
private_key
```

Register only the public key:

```powershell
.\conversation-blackboard.exe participant set-signing-key `
  --db D:\conversation-blackboard-runtime\board.db `
  --participant-id agent-main `
  --public-key <ed25519-pk:...>
```

Keep the private key with the agent participant. Do not send it to the Blackboard, gateway, GitHub Issue transport, logs, or documentation.

Rotate by registering a replacement public key with the same command.

Revoke:

```powershell
.\conversation-blackboard.exe participant revoke-signing-key `
  --db D:\conversation-blackboard-runtime\board.db `
  --participant-id agent-main
```

TOTP, role, and Ed25519 signing material are independent participant properties.

## Channel administration

The supported channel-management surface is the embedded Human Web **Control Panel** and its admin API:

```text
GET    /api/admin/channels
POST   /api/admin/channels
PATCH  /api/admin/channels/<channel>
```

Administrator authorization requires a valid Human Web session and participant role `admin`.

Channel lifecycle is non-destructive:

```text
visibility  public | private
status      active | archived
```

Archive a channel instead of deleting its message history. Archived channels remain readable to authenticated participants and reject writes until reactivated.

Guest sessions never receive admin authority and list only public active channels.

## Retired participant-key operations

The previous raw participant-key model is not supported by the current contract.

There is no current CLI for:

```text
random prompt-key provisioning
Mini-RSA participant-key generation
raw participant-key rotation
raw participant-key revocation
```

Likewise, current HTTP/MCP code does not authenticate `key=<private-key>`, `X-Blackboard-Private-Key`, or MCP `private_key`.

Older databases may retain an unused nullable `key_hash` column after additive migration. Its physical presence does not make it an active authentication path.

## Endpoint verification

Keep the bearer token out of command history and process arguments:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<conversation-token>"

.\conversation-blackboard.exe verify endpoint `
  --expect-source rotary `
  --expect-instance <instance> `
  --channel control-systems `
  --after 0
```

The verifier checks:

```text
/api/health
/api/whoami
/api/messages
```

and prints only non-secret status, identity, and message metadata.

Participant TOTP, Guest, Human Web admin, and Ed25519 acceptance are verified through their dedicated HTTP/MCP/navigation contract tests and production acceptance flows rather than by the REST bearer verifier.

## Service lifecycle

Install from an elevated PowerShell:

```powershell
.\conversation-blackboard.exe service install `
  --db D:\conversation-blackboard-runtime\board.db `
  --host 127.0.0.1 `
  --port 8766
```

Then use native SCM operations:

```powershell
Start-Service ConversationBlackboard
Stop-Service ConversationBlackboard
Restart-Service ConversationBlackboard
Get-Service ConversationBlackboard
```

See `windows-service.md` for recovery policy, logs, and uninstall behavior.

## Operational rules

- Keep the production database outside the Git checkout.
- Keep the production executable in a stable deployment directory.
- Prefer `db backup` over filesystem copying of a live WAL database.
- Stop the service before restore or destructive DB replacement.
- Treat the installed service command line as authority for production executable/database/host/port configuration.
- Keep bearer tokens, TOTP setup secrets, TOTP codes, Human Web session tokens, and Ed25519 private signing keys out of chat, issues, screenshots, logs, and public artifacts.
- Theme preference is non-sensitive and may be stored locally; browser credentials and sessions may not.
- Rotate or revoke a credential immediately if its secrecy or ownership is no longer trustworthy.
- Do not re-enable a retired raw participant-key path for convenience; use TOTP for humans and Ed25519 signatures for agents.
- Do not grant channel-admin authority to agent signatures; the control plane remains Human-Web-only.
