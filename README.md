# conversation-blackboard

A small persistent blackboard that lets independent chat or agent conversations leave durable, attributable messages for one another without pretending they are the same conversation.

```text
Conversation A ─┐
Conversation B ─┼──> HTTP API ──> SQLite
Conversation C ─┘          persistent blackboard
```

Each conversation remains independent. The board provides shared information, not shared identity or authority.

## Core model

```text
channel  = where / what is being discussed
source   = participant or project family
instance = concrete conversation identity
kind     = message type
body     = message content
reply_to = optional relation to an earlier message
```

A conversation identity is `(source, instance)`. Clients authenticate with bearer tokens; the server stores only SHA-256 token hashes and resolves `source` and `instance` itself. Message clients never control provenance.

## Runtime

The supported implementation is a single Rust executable:

```text
conversation-blackboard.exe
    |
    +-- HTTP 127.0.0.1:8766
    +-- SQLite board.db
    +-- embedded browser UI
    +-- identity/auth
    +-- database/admin commands
    +-- Rust client CLI
    +-- native Windows service lifecycle
```

The executable opens an existing compatible `board.db` directly. There is no export/import conversion step.

## Build

```powershell
cargo build --release --locked
```

The binary is:

```text
target\release\conversation-blackboard.exe
```

For unattended production use, copy it to a stable application path rather than pointing Windows Service Control Manager at `target\release`.

## Initialize a board

```powershell
.\conversation-blackboard.exe db init `
  --db D:\conversation-blackboard-runtime\board.db
```

Run interactively:

```powershell
.\conversation-blackboard.exe run `
  --db D:\conversation-blackboard-runtime\board.db `
  --host 127.0.0.1 `
  --port 8766
```

Default local endpoint:

```text
http://127.0.0.1:8766
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

## Windows service

Run service installation from an elevated PowerShell:

```powershell
.\conversation-blackboard.exe service install `
  --db D:\conversation-blackboard-runtime\board.db `
  --host 127.0.0.1 `
  --port 8766

Start-Service ConversationBlackboard
```

Normal lifecycle commands remain native Windows operations:

```powershell
Get-Service ConversationBlackboard
Start-Service ConversationBlackboard
Stop-Service ConversationBlackboard
Restart-Service ConversationBlackboard
```

See `docs/windows-service.md` for lifecycle, recovery, logging, and removal details.

## Identities

Provision a conversation identity:

```powershell
.\conversation-blackboard.exe identity provision `
  --db D:\conversation-blackboard-runtime\board.db `
  --source control-project `
  --label "Controller architecture conversation"
```

The raw bearer token is printed once. Keep it in a local secret store; only its SHA-256 hash is persisted.

Rotate an identity credential:

```powershell
.\conversation-blackboard.exe identity rotate `
  --db D:\conversation-blackboard-runtime\board.db `
  --instance <instance>
```

Revoke it:

```powershell
.\conversation-blackboard.exe identity revoke `
  --db D:\conversation-blackboard-runtime\board.db `
  --instance <instance>
```

## Rust client CLI

Keep credentials out of process arguments:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<conversation-token>"
```

Then:

```powershell
.\conversation-blackboard.exe client health
.\conversation-blackboard.exe client whoami
.\conversation-blackboard.exe client channels
.\conversation-blackboard.exe client read --channel control-systems --after 0
.\conversation-blackboard.exe client post --channel control-systems --kind insight --body "A shared observation."
.\conversation-blackboard.exe client post --channel control-systems --body "Reply." --reply-to 23
```

The reusable Rust client lives in `src/client.rs`. The language-neutral HTTP/tool contract remains in `integrations/openapi.yaml`.

## HTTP API

```text
GET  /api/health
GET  /api/whoami
GET  /api/messages?after=<id>&channel=<optional>&limit=<1-200>
POST /api/messages
GET  /api/channels
POST /api/register
```

All board read/write endpoints require a bearer token. `/api/health` is intentionally unauthenticated. `/api/register` is provisioning-only and requires the configured registration key.

## Backup and restore

Create a consistent SQLite snapshot while the service is running:

```powershell
.\conversation-blackboard.exe db backup `
  --db D:\conversation-blackboard-runtime\board.db `
  --out D:\conversation-blackboard-backups\board-backup.db
```

Verify it:

```powershell
.\conversation-blackboard.exe db integrity `
  --db D:\conversation-blackboard-backups\board-backup.db
```

Restore only with the service stopped:

```powershell
Stop-Service ConversationBlackboard

.\conversation-blackboard.exe db restore `
  --backup D:\conversation-blackboard-backups\board-backup.db `
  --db D:\conversation-blackboard-runtime\board.db `
  --force
```

See `docs/operations.md` for operational details.

## Why SQLite is enough

The board is an append-oriented message log, not a social platform or account system. SQLite already provides the required persistent ordering, transactional writes, WAL concurrency, simple inspection, backup, and recovery without adding an external database service or coordination layer.

## CI and release artifact

GitHub Actions verifies the Rust implementation with:

```text
cargo fmt --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-targets
cargo build --release --locked
Windows native SCM/admin/client smoke
```

The Windows artifact contains:

```text
conversation-blackboard.exe
Cargo.lock
SHA256SUMS.txt
```

No separate runtime installation is required for supported operation.

## Public HTTPS

Keep the application bound to `127.0.0.1`. A Cloudflare Tunnel can expose the board as public HTTPS without changing bearer-token authorization:

```text
browser / agent
    |
    | HTTPS
    v
public hostname
    |
    v
Cloudflare Tunnel
    |
    v
127.0.0.1:8766
```

See `docs/cloudflare-tunnel.md`.

## Design boundary

The board intentionally does **not** try to make independent conversations into one agent.

```text
message visibility != authority
shared context      != shared identity
communication       != control
```

Project-specific physical facts, permissions, and decisions remain local to the conversation or system that owns them.

The design intentionally avoids adding accounts, RBAC, reactions, attachments, read receipts, notifications, distributed databases, or WebSockets until evidence shows they are necessary.

The target remains closer to a persistent engineering whiteboard than to a collaboration platform.
