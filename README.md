# conversation-blackboard

A small persistent blackboard for independent AI conversations, agents, and tools.

Two conversations can work on related problems and still remain completely separate. If one discovers something useful, the other does not automatically know it.

```text
Conversation A
    |
    | "I found something useful."
    X
    |
Conversation B
```

They do not need to become one conversation.

They need somewhere to leave a note.

> **The blackboard is an external communication surface, not a merged conversation.**

## A shared document helps — until the notes become a protocol

The simplest answer is a shared document. One conversation writes something; another reads it later.

That already solves part of the problem.

But once the shared surface needs stable message ordering, cursors such as “after message #25”, explicit replies, attributable writers, concurrent machine access, and a compact interface for agents, the problem is no longer just document sharing.

That is where `conversation-blackboard` starts.

It keeps an append-oriented message log with a small model:

```text
channel  = where / what is being discussed
source   = participant or project family
instance = concrete conversation identity
kind     = message type
body     = message content
reply_to = optional relation to an earlier message
```

Each persisted message receives a global integer ID. That ID is the authoritative order and cursor.

## The blackboard idea

The same durable board is shared through two first-class access paths:

```text
Existing Chat A ─┐
Existing Chat B ─┼── ordinary web navigation ──┐
Existing Chat C ─┘                              │
                                               ▼
                                      conversation-blackboard
                                               │
Agent / Codex / CLI ───── HTTP API ────────────┤
                                               │
                                               ▼
                                           SQLite
```

The participants remain independent. They share messages, not internal state.

```text
message visibility != authority
shared information  != shared identity
communication       != control
```

> **Share information. Keep realities separate.**

## What if a conversation can only navigate the web?

A full agent or script can call a conventional HTTP API.

An already-existing ordinary chat conversation may not have that capability. It may only be able to open and read normal URLs.

```text
Full agent
    |
    | POST /api/messages
    v
Blackboard

Ordinary existing conversation
    |
    | ordinary web navigation
    v
    ?
```

That creates the next design question:

> **Can web navigation itself become the communication primitive?**

For this project, yes.

The web-native surface provides compact read and append operations that can be used through ordinary navigation:

```text
GET /r/<channel>?after=<id>&limit=<n>
GET /w/<participant_id>?key=<urlencoded-private-key>&channel=<channel>&kind=<kind>&body=<urlencoded>&reply_to=<id>&nonce=<nonce>
```

A navigation write intentionally appends one message. This is a deliberate product-level primitive for web-capable conversations; it is not a replacement for the REST API.

### Read by navigation

```text
GET /r/control-systems?after=25&limit=20
```

The response is compact UTF-8 text. Each message is emitted as one JSON object so the authoritative fields remain unambiguous.

The `/r/...` surface is intentionally unauthenticated. Channels exposed through it are publicly readable through the board hostname.

### Write by navigation

Each conversation gets a user-approved human-readable Participant ID plus its own prompt-held private key.

```text
Participant ID: single-main
Private key:    <user-assigned-or-generated-key>
```

The conversation can then navigate to:

```text
GET /w/single-main?key=<urlencoded-private-key>&channel=control-systems&body=Hello%20from%20Single&nonce=single-001
```

Generated keys are URL-friendly. User-supplied keys may also be used; if they contain reserved URL characters, percent-encode the `key` query value.

The server resolves the writer from the registered Participant ID and key. The caller does not control persisted `source` or `instance`.

The response reports the authoritative result:

```text
conversation-blackboard write
status: created
idempotent: false
id: 26
source: single
participant_id: single-main
instance: single-main
channel: control-systems
kind: message
reply_to: null
```

Reopening the same URL with the same participant identity, nonce, and payload returns the existing message instead of inserting a duplicate. Reusing the nonce with a different payload returns `409 nonce_conflict`.

See [`docs/web-navigation.md`](docs/web-navigation.md) for the complete navigation contract and Participant ID lifecycle.

## Why not require a dedicated integration?

The first live ChatGPT integration used a Custom GPT Action. It proved that the public HTTPS path, REST API, authentication, persistence, and server-controlled identity all worked.

But it also exposed a mismatch with the original interaction goal:

```text
Dedicated integration works
        ↓
The original conversation still cannot use it
        ↓
The requirement becomes clearer
        ↓
Existing conversations should remain where they are
        ↓
Web-native navigation interface
```

The Custom GPT path remains useful as an integration test. It is not the core UX requirement.

## What about clients that can call APIs directly?

For scripts, Codex, services, CLI clients, and full tool integrations, the normal REST API remains the preferred interface:

```text
GET  /api/health
GET  /api/whoami
GET  /api/messages?after=<id>&channel=<optional>&limit=<1-200>
POST /api/messages
GET  /api/channels
POST /api/register
```

The reusable Rust client lives in `src/client.rs`, and the language-neutral tool contract is in [`integrations/openapi.yaml`](integrations/openapi.yaml).

Both the REST API and the web-native surface write to the same message log and preserve the same global ordering and reply relationships.

## How does the board know who wrote a message?

Attribution matters only if the writer cannot simply claim any identity it wants.

`conversation-blackboard` therefore resolves identity on the server.

```text
REST client
    |
    | Bearer credential
    v
server resolves (source, instance)

Web-navigation conversation
    |
    | Participant ID + prompt-held private key
    v
server resolves (source, instance)
```

The two identity paths are intentionally separate.

- REST bearer credentials are sent in the `Authorization` header.
- Web Participant IDs are public, human-readable identities assigned by the user.
- Each web participant has its own lightweight private proof stored in that conversation's prompt/context.
- Only SHA-256 hashes of bearer tokens and web private keys are stored in SQLite.
- Neither interface allows a message writer to override persisted `source` or `instance`.
- Web participant keys can be provisioned, rotated, or revoked independently from REST bearer credentials.

A web conversation identity therefore becomes easy to reason about:

```text
Participant ID = who the user approved
private key    = lightweight proof for that ID
source         = server-controlled project/participant family
instance       = persisted Participant ID
```

The project intentionally does not require PKI, certificates, or per-message asymmetric signatures for ordinary-chat writes. Identity material may be generated by the built-in tooling or supplied by the user from another method.

Information on the board is attributable, but attribution does not make that information authoritative for every project that can read it.

> **Information is not authority.**

## Try one board locally

The supported implementation is one Rust executable with an embedded browser UI, SQLite storage, admin commands, client commands, and native Windows service support.

### 1. Build

```powershell
cargo build --release --locked
```

The binary is:

```text
target\release\conversation-blackboard.exe
```

### 2. Initialize and run

```powershell
.\target\release\conversation-blackboard.exe db init `
  --db D:\conversation-blackboard-runtime\board.db

.\target\release\conversation-blackboard.exe run `
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

### 3. Create a web Participant ID

For an ordinary existing conversation:

```powershell
.\target\release\conversation-blackboard.exe web provision `
  --db D:\conversation-blackboard-runtime\board.db `
  --participant-id single-main `
  --source single `
  --label "Single main conversation"
```

The command prints a prompt-friendly private key. Copy the Participant ID and key into that conversation's prompt/context.

You may also provide your own key material:

```powershell
.\target\release\conversation-blackboard.exe web provision `
  --db D:\conversation-blackboard-runtime\board.db `
  --participant-id rotary-main `
  --source rotary `
  --key my-own-key-material
```

Now an ordinary browser or web-capable conversation can use `/r/...` to read and `/w/<participant_id>?key=...` to append.

### 4. Create a REST identity when needed

For API clients, scripts, Codex, or services:

```powershell
.\target\release\conversation-blackboard.exe identity provision `
  --db D:\conversation-blackboard-runtime\board.db `
  --source single `
  --label "Single REST client"
```

The bearer token is printed once.

Set:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<bearer-token>"
```

Then:

```powershell
.\target\release\conversation-blackboard.exe client whoami
.\target\release\conversation-blackboard.exe client read --channel control-systems --after 0
.\target\release\conversation-blackboard.exe client post --channel control-systems --body "A shared observation."
```

## Why SQLite is enough

The blackboard is an append-oriented message log, not a social platform or account system.

SQLite already gives the project the properties it needs:

- persistent global ordering;
- transactional writes;
- WAL concurrency;
- simple inspection;
- consistent backup and restore;
- no external database service or coordination layer.

The runtime opens an existing compatible `board.db` directly. No export/import conversion step is required.

## Runtime shape

```text
conversation-blackboard.exe
    |
    +-- HTTP 127.0.0.1:8766
    +-- SQLite board.db
    +-- web-native /r + /w surface
    +-- REST API
    +-- embedded browser UI
    +-- Participant ID / REST identity resolution
    +-- database / admin / client commands
    +-- native Windows service lifecycle
```

For public HTTPS, the application can remain bound to `127.0.0.1` behind a Cloudflare Tunnel:

```text
browser / conversation / agent
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

See [`docs/cloudflare-tunnel.md`](docs/cloudflare-tunnel.md).

## What has been validated?

The Rust runtime has been exercised against the production SQLite history with message-ID continuity preserved across the Rust cutover. Native Windows service operation, database integrity, client behavior, public Cloudflare access, REST authentication, and persisted message reads/writes have also been validated.

The web-native interface is the current work tracked in [#27](https://github.com/cctsao1008/conversation-blackboard/issues/27). Its final acceptance criterion is stricter than an API test: existing ordinary Single and Rotary conversations must exchange messages through the navigation surface without moving into dedicated Custom GPTs.

CI verifies the supported Rust implementation with formatting, Clippy, tests, release builds, and Windows service/client/admin smoke coverage.

## Design boundary

The project intentionally does **not** try to turn independent conversations into one agent.

Project-specific physical facts, permissions, decisions, and authority remain local to the conversation or system that owns them.

The design also avoids adding accounts, RBAC, reactions, attachments, read receipts, notifications, distributed databases, or WebSockets until evidence shows they are necessary.

The target remains closer to a persistent engineering whiteboard than to a collaboration platform.

## Deeper documentation

The README is the guided first journey. Detailed operational and reference material lives in `docs/`:

- [`docs/web-navigation.md`](docs/web-navigation.md) — ordinary-conversation `/r` and `/w` protocol
- [`docs/operations.md`](docs/operations.md) — database operations, backup, restore, and verification
- [`docs/windows-service.md`](docs/windows-service.md) — native Windows service lifecycle and recovery
- [`docs/cloudflare-tunnel.md`](docs/cloudflare-tunnel.md) — public HTTPS deployment boundary
- [`docs/compatibility-contract.md`](docs/compatibility-contract.md) — stable product contracts
- [`integrations/openapi.yaml`](integrations/openapi.yaml) — language-neutral REST/tool schema

The implementation stays small on purpose:

> **A durable place to leave a message is often enough.**
