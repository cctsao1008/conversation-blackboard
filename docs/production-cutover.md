# Production deployment and rollback

The supported production shape is a native Windows service using one SQLite database and one active writer runtime.

```text
Windows SCM
    |
    v
ConversationBlackboard
    |
    v
conversation-blackboard.exe
    |
    +-- HTTP 127.0.0.1:8766
    +-- board.db
    +-- embedded browser UI
    +-- Rust client/admin commands
```

## Stable executable path

Do not point SCM at a disposable Cargo build directory. Stage the release executable in a stable path such as:

```text
D:\conversation-blackboard\bin\conversation-blackboard.exe
```

Build with the committed dependency graph:

```powershell
cargo build --release --locked
```

Optionally verify the staged file against the build output with `Get-FileHash` before installation.

## Pre-deployment backup

Create an integrity-checked SQLite snapshot before service changes:

```powershell
$bb = "D:\conversation-blackboard\bin\conversation-blackboard.exe"
$prodDb = "D:\conversation-blackboard-runtime\board.db"
$backup = "D:\conversation-blackboard-backups\board-pre-deploy.db"

& $bb db backup --db $prodDb --out $backup
& $bb db integrity --db $backup
```

Expected integrity result:

```text
ok
```

## Install the Windows service

Run from an elevated PowerShell:

```powershell
& $bb service install `
  --db $prodDb `
  --host 127.0.0.1 `
  --port 8766

Start-Service ConversationBlackboard
Get-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

The service is installed with Automatic startup.

## Authenticated verification

Keep the token in the process environment rather than command arguments:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<conversation-token>"

& $bb verify endpoint `
  --expect-source <source> `
  --expect-instance <instance> `
  --after 0
```

Verify the browser UI separately at:

```text
http://127.0.0.1:8766/
```

## SCM restart and integrity

```powershell
Restart-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health

Stop-Service ConversationBlackboard
& $bb db integrity --db $prodDb
Start-Service ConversationBlackboard
```

The database should remain intact across normal stop/start/restart operations.

## Post-deployment backup

```powershell
$post = "D:\conversation-blackboard-backups\board-post-deploy.db"

& $bb db backup --db $prodDb --out $post
& $bb db integrity --db $post
```

## Reboot verification

After a Windows reboot:

```powershell
Get-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Expected state:

```text
ConversationBlackboard = Running
health = ok
```

## Rollback

Rollback uses a known-good Rust executable and verified SQLite backup.

```text
1. Stop ConversationBlackboard.
2. Preserve the current database for diagnosis.
3. Verify the selected backup with `db integrity`.
4. Restore the backup with `db restore --force` while the service is stopped.
5. If required, stage the previous known-good executable.
6. Start the service.
7. Verify health, identity, history, and message ordering.
```

Example restore:

```powershell
Stop-Service ConversationBlackboard

& $bb db restore `
  --backup D:\conversation-blackboard-backups\board-known-good.db `
  --db $prodDb `
  --force

& $bb db integrity --db $prodDb
Start-Service ConversationBlackboard
```

## Production invariants

```text
one production DB
one active service writer
localhost-only origin
backup before destructive changes
integrity check after restore
identity provenance remains server-controlled
message IDs remain SQLite-authoritative global order
```

Do not expose bearer tokens in chat, issues, logs, screenshots, or command-line arguments. Rotate a credential immediately if it leaves its intended secret store.
