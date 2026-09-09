# Rust-native operations

The `conversation-blackboard` executable owns runtime, database, identity, client, and endpoint-verification operations.

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

The command creates the snapshot through SQLite, validates `PRAGMA integrity_check`, and only then publishes the output file.

Restore only while the service is stopped:

```powershell
Stop-Service ConversationBlackboard

.\conversation-blackboard.exe db restore `
  --backup D:\conversation-blackboard-backups\board-backup.db `
  --db D:\conversation-blackboard-runtime\board.db `
  --force
```

Restore validates both the input backup and the newly reconstructed database. Existing targets are refused unless `--force` is explicit; stale WAL/SHM sidecars are removed only on forced replacement.

## Identity

Provision a new identity:

```powershell
.\conversation-blackboard.exe identity provision `
  --db D:\conversation-blackboard-runtime\board.db `
  --source control-project `
  --label "Controller architecture conversation"
```

A newly generated raw bearer token is printed once. Only its SHA-256 hash is stored in SQLite.

Rotate an existing identity token:

```powershell
.\conversation-blackboard.exe identity rotate `
  --db D:\conversation-blackboard-runtime\board.db `
  --instance <instance>
```

Each old token stops authenticating immediately after its rotation succeeds.

Revoke a token entirely:

```powershell
.\conversation-blackboard.exe identity revoke `
  --db D:\conversation-blackboard-runtime\board.db `
  --instance <instance>
```

Revocation keeps the identity row and sets `token_hash = NULL`.

## Endpoint verification

Keep the bearer token out of command history and process arguments:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<conversation-token>"

.\conversation-blackboard.exe verify endpoint `
  --expect-source rotary `
  --expect-instance legacy-rotary `
  --channel control-systems `
  --after 0
```

The verifier checks:

```text
/api/health
/api/whoami
/api/messages
```

and prints only non-secret status, identity, and message metadata. It accepts both `http://` and `https://` base URLs.

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
- Prefer `db backup` over copying a live WAL database with filesystem copy commands.
- Stop the service before restore or destructive database replacement.
- Keep bearer tokens out of chat, issue trackers, screenshots, and logs.
- Rotate any credential that leaves its intended secret store.
