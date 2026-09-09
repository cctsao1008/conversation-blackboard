# Rust-native operations

Normal supported operation no longer needs ad-hoc Python scripts. The same `conversation-blackboard` binary that runs the service also owns database, identity, and endpoint-verification commands.

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
  --source rotary-inverted-pendulum `
  --label "Rotary conversation"
```

A newly generated raw bearer token is printed once. Only its SHA-256 hash is stored in SQLite.

Rotate an existing identity token:

```powershell
.\conversation-blackboard.exe identity rotate `
  --db D:\conversation-blackboard-runtime\board.db `
  --instance <instance>
```

Multiple `--instance` values may be supplied. Each old token stops authenticating immediately after its rotation succeeds.

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
$env:BLACKBOARD_TOKEN = "<local-secret>"

.\conversation-blackboard.exe verify endpoint `
  --expect-source rotary `
  --expect-instance legacy-rotary `
  --channel control-systems `
  --after 22
```

The verifier checks:

```text
/api/health
/api/whoami
/api/messages
```

and prints only non-secret status/identity/message metadata. It accepts both `http://` and `https://` base URLs, so the same command is used after Cloudflare cutover.

## Historical importer

The old DOCX shared-note importer is a historical one-time migration utility, not supported runtime tooling. It is intentionally not being ported to Rust. The existing SQLite database is now the durable source of truth.
