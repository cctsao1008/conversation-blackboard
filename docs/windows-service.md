# Native Windows service

The production Rust runtime is designed to be owned by Windows Service Control Manager (SCM), not by an open PowerShell window.

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
    +-- SQLite board.db
    +-- embedded browser UI
```

The service implementation uses the native Windows service APIs through the Rust `windows-service` crate. Interactive `run` mode remains available for development.

## Build

```powershell
git pull
cargo build --release
```

The executable is:

```text
target\release\conversation-blackboard.exe
```

For a production installation, place that executable in a stable application directory before installing the service. Do not make the service depend on a temporary build directory that may be deleted.

Keep the database outside the Git checkout as well, for example:

```text
D:\conversation-blackboard-runtime\board.db
```

## Safe copy of an active WAL database

`conversation-blackboard` uses SQLite WAL mode. When the source server is running, recent committed rows can still live in `board.db-wal` rather than the main `board.db` file.

Do **not** create a verification database with a plain filesystem copy such as:

```powershell
Copy-Item D:\sqlite-tools-win-x64-3530400\board.db D:\conversation-blackboard-runtime\board-service-test.db
```

while the source runtime may still be active. Such a copy can be structurally valid yet omit recent messages, identities, or rotated token hashes.

Use SQLite's online backup API instead. With the SQLite CLI already installed locally:

```powershell
Remove-Item D:\conversation-blackboard-runtime\board-service-test.db -ErrorAction SilentlyContinue

& D:\sqlite-tools-win-x64-3530400\sqlite3.exe `
  D:\sqlite-tools-win-x64-3530400\board.db `
  ".backup 'D:/conversation-blackboard-runtime/board-service-test.db'"
```

The repository's current Python backup helper also uses SQLite's online backup API and is safe during the migration period:

```powershell
py .\tools\backup_db.py `
  --db D:\sqlite-tools-win-x64-3530400\board.db `
  --out D:\conversation-blackboard-runtime\board-service-test.db
```

Issue #23 will replace the temporary Python operational helper with a Rust-native command. The required property is the SQLite backup operation, not the implementation language used during migration.

If an existing bearer token works against the source runtime but not against a plain copied database, treat that as evidence that the filesystem copy missed WAL-resident state. Recreate the verification DB with an online backup before investigating auth code.

Bearer tokens are credentials. Keep them local; if a raw token is pasted into chat, an issue, a log, or another non-secret channel, rotate it before production use.

## Install

Run an elevated PowerShell from the directory containing the production executable:

```powershell
.\conversation-blackboard.exe service install `
  --db D:\conversation-blackboard-runtime\board.db `
  --host 127.0.0.1 `
  --port 8766
```

Installation configures:

```text
Service name:     ConversationBlackboard
Display name:     Conversation Blackboard
Account:          LocalSystem
Startup:          Automatic
Origin:           127.0.0.1:8766
Failure recovery: restart after 5 s, 15 s, then 30 s
```

Only non-secret runtime values are placed in the SCM command line. Bearer tokens and the registration key are never service arguments.

`/api/register` remains disabled unless `BLACKBOARD_REGISTRATION_KEY` is available to the service process. Existing bearer-token identities do not require that key for normal read/write use.

## Control

Normal Windows commands work:

```powershell
Get-Service ConversationBlackboard
Start-Service ConversationBlackboard
Stop-Service ConversationBlackboard
Restart-Service ConversationBlackboard
```

The executable also exposes equivalent commands:

```powershell
.\conversation-blackboard.exe service status
.\conversation-blackboard.exe service start
.\conversation-blackboard.exe service stop
.\conversation-blackboard.exe service restart
.\conversation-blackboard.exe service uninstall
```

Install/uninstall require an elevated terminal. Start/stop permissions follow normal Windows service ACLs.

## Graceful stop

SCM `Stop` and Windows `Shutdown` controls signal the Axum/Tokio graceful-shutdown path. The HTTP listener exits before the service reports `Stopped`; SQLite connections are request-scoped, so no long-lived connection is abandoned by normal service stop/restart.

The database continues to use the existing WAL + busy-timeout contract.

## Service log

Lifecycle-only service logging is written beside the configured database:

```text
D:\conversation-blackboard-runtime\conversation-blackboard.log
```

The service log records start/running/clean-stop/runtime-error events and does not log Authorization headers, bearer tokens, registration keys, request bodies, or board messages.

## Verification

After install:

```powershell
Start-Service ConversationBlackboard
Get-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Expected service state and HTTP result:

```text
Status : Running
{"status":"ok"}
```

Then verify an existing identity using a token kept only in the current PowerShell session:

```powershell
$token = Read-Host "Bearer token"
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod http://127.0.0.1:8766/api/whoami -Headers $headers
```

Finally verify restart persistence:

```powershell
Restart-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health
Get-Service ConversationBlackboard
```

The existing messages, identities, token hashes, and next SQLite message id must remain unchanged across service restart.

## Failure recovery

Installation configures SCM recovery actions and enables them for non-crash failures. Normal administrative `Stop-Service` is still a clean service stop and should not be treated as a runtime crash.

A destructive crash/recovery test should only be performed against a disposable SQLite-backup copy of `board.db` until the production cutover checklist in the Rust migration roadmap is complete.
