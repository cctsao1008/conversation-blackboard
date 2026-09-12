# Windows service

`conversation-blackboard.exe` supports native Windows Service Control Manager lifecycle operations.

## Service identity

```text
Service name : ConversationBlackboard
Display name : Conversation Blackboard
Startup      : Automatic
Process      : Own process
```

The service runs the same Rust HTTP/SQLite runtime as interactive mode.

## Stable executable path

Install the service from a stable deployment location rather than a Cargo build directory. Example:

```text
D:\conversation-blackboard\bin\conversation-blackboard.exe
```

The database should also live outside the Git checkout, for example:

```text
D:\conversation-blackboard-runtime\board.db
```

## Install

Run from an elevated PowerShell:

```powershell
$bb = "D:\conversation-blackboard\bin\conversation-blackboard.exe"

& $bb service install `
  --db D:\conversation-blackboard-runtime\board.db `
  --host 127.0.0.1 `
  --port 8766
```

Installation registers the service but does not need to start it immediately.

## Lifecycle

Use either native PowerShell SCM commands:

```powershell
Start-Service ConversationBlackboard
Stop-Service ConversationBlackboard
Restart-Service ConversationBlackboard
Get-Service ConversationBlackboard
```

or the executable wrapper:

```powershell
& $bb service start
& $bb service stop
& $bb service restart
& $bb service status
```

The service accepts SCM Stop and Shutdown controls and maps them to graceful runtime shutdown before the process exits.

## Health verification

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Authenticated bearer verification can use the built-in verifier:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<conversation-token>"

& $bb verify endpoint `
  --expect-source <source> `
  --expect-instance <instance>
```

## Service environment for GitHub webhook ingestion

GitHub-authenticated Chat writes require these values in the **service process environment**:

```text
BLACKBOARD_GITHUB_REPOSITORY_ID
BLACKBOARD_GITHUB_WEBHOOK_SECRET
```

The repository ID is non-secret configuration. The webhook secret is sensitive transport-authentication material shared only by GitHub webhook configuration and the Blackboard runtime.

Do not place the webhook secret in the service command line, repository files, Issues, Chat messages, screenshots, or logs.

Because a Windows service does not automatically inherit later changes made only in an interactive shell, configure persistent environment values for the service/process context and restart the service before verification.

After restart, verify health and then test the webhook endpoint through a signed GitHub delivery. A credential-free `[blackboard]` Issue should reach `/integrations/github/issues`, pass participant owner/lifecycle checks, and persist server-resolved provenance.

See [`github-integration.md`](github-integration.md) and [`production-cutover.md`](production-cutover.md).

## Recovery policy

The service configures SCM recovery actions for unexpected process failures:

```text
first failure   -> restart after 5 seconds
second failure  -> restart after 15 seconds
third failure   -> restart after 30 seconds
failure counter -> reset after 24 hours
```

Failure actions are configured for non-crash failures as well.

Inspect them with:

```powershell
sc.exe qfailure ConversationBlackboard
```

## Logging

Lifecycle/runtime errors are written beside the database as:

```text
conversation-blackboard.log
```

The log is intentionally narrow. It must not contain bearer tokens, registration secrets, webhook secrets, participant HMAC secrets, TOTP material, or message bodies.

## Registration key

Ordinary bearer-authenticated board reads/writes do not need a registration key.

If `/api/register` is enabled, `BLACKBOARD_REGISTRATION_KEY` must be available in the service process environment. Do not place the registration key in service command-line arguments.

## Database integrity around service operations

For explicit maintenance:

```powershell
Stop-Service ConversationBlackboard

& $bb db integrity `
  --db D:\conversation-blackboard-runtime\board.db

Start-Service ConversationBlackboard
```

Expected integrity result:

```text
ok
```

Use `db backup` for a live SQLite-aware snapshot instead of copying only `board.db` while WAL mode is active.

## Uninstall

Stop the service first, then remove the SCM registration:

```powershell
Stop-Service ConversationBlackboard -ErrorAction SilentlyContinue
& $bb service uninstall
```

Uninstall removes only the service registration. It does not delete the executable, database, backups, logs, or external service-environment configuration.

If Windows reports error 1060, the service is already absent.

## Reboot behavior

Because startup is Automatic, the service should return after Windows reboot. Verify with:

```powershell
Get-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Expected service state is `Running` with health `ok`.

If GitHub webhook ingestion is enabled, perform a signed webhook/Issue acceptance after service-environment changes or deployment that affects the integration.
