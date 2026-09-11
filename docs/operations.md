# Rust-native operations

The `conversation-blackboard` executable owns runtime, database, identity, client, and endpoint-verification operations.

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

After a successful build it verifies the executable can start its CLI, records the current Git commit and SHA-256, and writes an ignored release manifest under `target/release/`. The Windows deployment script requires that manifest and refuses to deploy if the current `HEAD` or binary hash no longer matches it.

The script deliberately does **not** run `git pull`. It validates the checkout the operator explicitly selected.

## Guarded Windows production deployment

Run from an elevated PowerShell after release verification:

```powershell
.\scripts\deploy-windows.ps1
```

Production filesystem paths are not hard-coded in the script. The repository/build path is derived from the script location. The installed Windows service is the runtime authority for the production executable, database path, host, and port.

`ConversationBlackboard` is the normal service identity. An alternate installed instance can be selected explicitly:

```powershell
.\scripts\deploy-windows.ps1 -ServiceName <service-name>
```

Deployment always begins with a read-only preflight before any production mutation. The preflight validates the verified release manifest and binary hash, installed service configuration, current service health, and production database integrity. Any failure aborts before backup, service stop, or executable replacement.

The mutation phase then:

```text
create and verify SQLite-aware DB backup
preserve the currently installed executable
stop service and wait for Stopped
replace executable
verify installed SHA-256 equals verified release SHA-256
start service and wait for Running
verify /api/health
verify production database integrity again
```

If replacement or post-replacement startup/health verification fails, the script attempts to restore the preserved executable and return the service to `Running`.

For a read-only dry run, use standard PowerShell `-WhatIf` semantics:

```powershell
.\scripts\deploy-windows.ps1 -WhatIf
```

`-WhatIf` performs discovery and preflight but does not create backups, stop the service, copy the executable, or start the service.

The operational boundary is intentional: ordinary local build/test work does not need a preflight; operations that mutate authoritative production state do.

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

The paths above are examples only. Production deployment discovers the actual runtime database from the installed service configuration.

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
- Treat the installed service command line as the authority for production executable/database/host/port configuration.
- Do not guess production filesystem paths when the service configuration can provide them.
- Keep bearer tokens and participant private keys out of chat, issue trackers, screenshots, and logs.
- Rotate any credential that leaves its intended secret store.
