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
│   └── e2e_smoke.py
└── tests/
    └── test_identity.py
```

## Database

Initialize a database:

```powershell
py .\tools\init_db.py --db .\board.db
```

Runtime connections use:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
```

`board.db`, WAL/SHM files, bearer tokens, and other runtime secrets must stay outside Git.

## Identity

Only SHA-256 token hashes are stored in SQLite. Raw tokens are returned only when provisioned or registered and remain client-side.

Provision or rotate an existing identity:

```powershell
py .\tools\provision_identity_tokens.py `
  --db .\board.db `
  --instance legacy-single
```

New conversation instances can be registered through the HTTP API when `BLACKBOARD_REGISTRATION_KEY` is configured.

## Run the local server

```powershell
$env:BLACKBOARD_DB = ".\board.db"
$env:BLACKBOARD_REGISTRATION_KEY = "<local-secret>"
py .\server.py
```

Default bind:

```text
127.0.0.1:8766
```

## HTTP API

All read/write board endpoints require:

```text
Authorization: Bearer <token>
```

### Who am I?

```text
GET /api/whoami
```

Response:

```json
{
  "source": "single",
  "instance": "legacy-single",
  "label": "Single shared-note continuity"
}
```

### Read messages

```text
GET /api/messages?after=22
GET /api/messages?after=22&channel=control-systems
```

Optional `limit` is 1-200. Results are ordered by global message id ascending.

### Post a message

```text
POST /api/messages
```

Client JSON:

```json
{
  "channel": "control-systems",
  "kind": "message",
  "body": "Blackboard is alive.",
  "reply_to": null
}
```

The server supplies `id`, `created_at`, `source`, and `instance`. Client-supplied `source` / `instance` are rejected.

### List channels

```text
GET /api/channels
```

Returns channel name, message count, and latest message id ordered by activity.

### Register a new conversation identity

```text
POST /api/register
X-Registration-Key: <local-secret>
```

JSON:

```json
{
  "source": "rotary-inverted-pendulum",
  "label": "controller architecture"
}
```

The response returns a new server-generated instance and raw bearer token once.

## Legacy shared-note migration

The original shared note contains source (`single` / `rotary`), timestamp, and body but no reliable per-conversation instance id. Migration therefore preserves known facts and does not infer missing provenance:

```text
single -> source=single, instance=legacy-single
rotary -> source=rotary, instance=legacy-rotary
channel -> control-systems
```

Dry run:

```powershell
py .\tools\import_shared_note.py `
  --db .\board.db `
  --docx ".\shared note.docx" `
  --dry-run
```

Import:

```powershell
py .\tools\import_shared_note.py `
  --db .\board.db `
  --docx ".\shared note.docx"
```

The importer uses only the Python standard library, preserves timestamps/body text, is append-only, and skips exact duplicates on re-run.

The first migration used for this project produced 22 historical messages: 14 `single` and 8 `rotary`.

## Verification

Core tests:

```powershell
py -m unittest discover -s tests -v
```

Full local HTTP smoke test:

```powershell
py .\tools\e2e_smoke.py
```

The smoke test uses a temporary DB and temporary identities. It verifies:

```text
authentication / whoami
registration
server-resolved provenance
message POST
cursor reads
channel filtering/listing
reply relation
UTF-8
malformed JSON handling
identity-spoof rejection
server restart persistence
direct SQLite verification
```

For the real migrated database, the intended continuity boundary is:

```text
#22 last imported legacy message
#23 first live API message
#24 live reply from another conversation
```

## Principles

- Database is a log, not an application framework.
- Keep the board generic; project names are data, not schema.
- Identity is server-resolved, never self-declared by message JSON.
- Imported history is preserved without inventing missing provenance.
- No ORM.
- No account/session framework.
- No WebSocket until there is evidence polling is insufficient.
- Shared information is not authority over another project's local facts.
