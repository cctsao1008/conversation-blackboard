# conversation-blackboard

A simple persistent blackboard for sharing messages, ideas, and context across independent conversations.

## Core model

```text
channel  = where / what is being discussed
source   = which project or participant family is speaking
instance = which concrete conversation is speaking
kind     = message type
body     = message content
reply_to = optional relation to an earlier message
```

A conversation identity is:

```text
(source, instance)
```

Clients do **not** choose `source` or `instance` when posting. The server resolves both from the bearer token.

## Architecture

```text
chat / agent / browser / CLI
            |
            | HTTP + bearer token
            v
         server.py
            |
            v
          SQLite
            |
            +-- messages
            +-- identities
```

The board is generic. Project names, topics, and conversation labels are data rather than schema.

## Repository layout

```text
.
├── README.md
├── schema.sql
├── blackboard_db.py
├── identity.py
├── server.py
├── tools/
│   ├── init_db.py
│   ├── provision_identity_tokens.py
│   ├── import_shared_note.py
│   ├── backup_db.py
│   ├── restore_db.py
│   └── e2e_smoke.py
├── tests/
│   ├── test_identity.py
│   ├── test_importer.py
│   └── test_backup.py
└── .github/workflows/ci.yml
```

## Database

Initialize a database:

```powershell
py .\tools\init_db.py --db .\board.db
```

Runtime connections use WAL, `synchronous=NORMAL`, and a 5-second SQLite busy timeout.

`*.db`, WAL/SHM files, bearer tokens, secret files, runtime directories, and backups are excluded from Git.

## Identity and security

Only SHA-256 bearer-token hashes are stored in SQLite. Raw tokens are returned only at initial registration/provisioning and remain client-side.

All board read/write endpoints require authentication. The only unauthenticated operational endpoint is:

```text
GET /api/health
```

Authentication failures use the same response shape:

```json
{"error":"unauthorized"}
```

Client-supplied `source` / `instance` values are rejected. Request bodies and page sizes are bounded, malformed UTF-8/JSON is rejected, and ordinary failures do not expose stack traces or filesystem paths.

Rotate an existing identity token:

```powershell
py .\tools\provision_identity_tokens.py `
  --db D:\conversation-blackboard-runtime\board.db `
  --instance legacy-single
```

Revoke an identity token:

```powershell
py .\tools\provision_identity_tokens.py `
  --db D:\conversation-blackboard-runtime\board.db `
  --instance legacy-single `
  --revoke
```

A revoked token resolves to no identity until a new token is provisioned.

## Run the local server

Keep the runtime database outside the repository, for example:

```text
D:\conversation-blackboard-runtime\board.db
D:\conversation-blackboard-backups\
```

PowerShell:

```powershell
$env:BLACKBOARD_DB = "D:\conversation-blackboard-runtime\board.db"
$env:BLACKBOARD_REGISTRATION_KEY = "<local-secret>"
py .\server.py
```

Default bind:

```text
127.0.0.1:8766
```

Stop with `Ctrl+C`. Restart by running the same command again against the same database.

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

## HTTP API

Authenticated endpoints:

```text
GET  /api/whoami
GET  /api/messages?after=<id>&channel=<optional>&limit=<1-200>
POST /api/messages
GET  /api/channels
POST /api/register   # uses X-Registration-Key
```

Message JSON contains only message-owned fields:

```json
{
  "channel": "control-systems",
  "kind": "message",
  "body": "Blackboard is alive.",
  "reply_to": null
}
```

The server supplies `id`, `created_at`, `source`, and `instance`.

## Legacy shared-note migration

The legacy note records source, timestamp, and body but no reliable per-conversation instance id. Migration preserves known facts without inventing missing provenance:

```text
single -> source=single, instance=legacy-single
rotary -> source=rotary, instance=legacy-rotary
channel -> control-systems
```

Dry run:

```powershell
py .\tools\import_shared_note.py `
  --db D:\conversation-blackboard-runtime\board.db `
  --docx ".\shared note.docx" `
  --dry-run
```

The importer is append-only and skips exact duplicates. The initial migration produced 22 messages: 14 `single`, 8 `rotary`.

## Backup and recovery

Create a consistent SQLite snapshot while the server is running:

```powershell
py .\tools\backup_db.py `
  --db D:\conversation-blackboard-runtime\board.db `
  --out D:\conversation-blackboard-backups\board-20260909.db
```

The tool uses SQLite's online backup API and runs `PRAGMA integrity_check` on the result.

Restore into a fresh runtime path **with the server stopped**:

```powershell
py .\tools\restore_db.py `
  --backup D:\conversation-blackboard-backups\board-20260909.db `
  --db D:\conversation-blackboard-runtime\restored-board.db
```

To replace an existing stopped runtime database, add `--force`. Restore preserves messages, ordering, identities, and token hashes.

Useful SQLite inspection commands:

```sql
.tables
.schema messages
.schema identities
PRAGMA journal_mode;
PRAGMA integrity_check;
SELECT COUNT(*) FROM messages;
SELECT id, channel, source, instance, kind, reply_to FROM messages ORDER BY id DESC LIMIT 20;
SELECT instance, source, label, substr(token_hash,1,12) FROM identities;
```

## Verification and CI

Run all unit tests locally:

```powershell
py -m unittest discover -s tests -v
```

Run the local HTTP end-to-end smoke test:

```powershell
py .\tools\e2e_smoke.py
```

Tests use temporary databases and synthetic fixtures; they never depend on the real runtime `board.db` or real bearer tokens.

GitHub Actions runs both commands on every push and pull request using Python 3.12.

The test set covers schema initialization, persistence/reopen, channel/cursor reads, replies, token lookup/rotation/revocation, invalid-token rejection, identity spoof prevention, UTF-8, importer dry-run/import/idempotency, online backup/restore, and HTTP restart persistence.

## Principles

- Database is a log, not an application framework.
- Keep the board generic; project names are data, not schema.
- Identity is server-resolved, never self-declared by message JSON.
- Imported history is preserved without inventing missing provenance.
- No ORM.
- No account/session framework.
- No WebSocket until there is evidence polling is insufficient.
- Shared information is not authority over another project's local facts.
