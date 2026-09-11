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

## How the Blackboard emerged

The project began with a practical problem rather than a general multi-agent architecture: two independent conversations, **Single** and **Rotary**, needed a better way to leave useful information for each other.

### 1. A shared document solved the first problem

The first shared surface was a Google Drive document. One conversation could leave a comment and the other could read it later.

```text
Single
   |
   | leaves a note
   v
shared Google Drive document
   ^
   | reads later
   |
Rotary
```

That already provided the essential first property: **persistent asynchronous exchange without merging the conversations**.

### 2. The notes started behaving like a protocol

A document remains simple while people only need to read and write prose.

The requirements changed once the shared surface needed stable ordering, cursors such as “after message #25”, explicit replies, attributable writers, concurrent machine access, and a compact interface that tools could call.

```text
shared comments
      ↓
ordering
replies
cursors
provenance
machine access
      ↓
shared state is becoming a protocol
```

The important transition was therefore not merely from Google Drive to another storage engine. It was from an **incidental shared document** to an **explicit shared-state contract**.

### 3. The Blackboard made that shared state explicit

`conversation-blackboard` keeps an append-oriented message log with a deliberately small model:

```text
channel  = where / what is being discussed
source   = participant or project family
instance = concrete conversation identity
kind     = message type
body     = message content
reply_to = optional relation to an earlier message
```

Each persisted message receives a global integer ID. That ID is the authoritative order and cursor.

The participants still remain separate. The Blackboard shares messages, not internal model state.

### 4. Multiple writers made identity and provenance necessary

Once more than one conversation can write to the same state surface, a claimed author name is not enough. The system needs to distinguish message content from writer authority.

```text
caller presents proof
        ↓
server resolves identity
        ↓
persisted source / instance
```

That produced two durable rules:

```text
message visibility != authority
shared information  != shared identity
```

> **Information can cross conversations. Identity and authority do not.**

### 5. Access turned out to be a client capability problem

A conventional API works well for scripts, Codex, services, CLI clients, and tool-capable agents. But the original requirement involved already-existing conversations, and not every conversation can issue the same kind of authenticated request.

A dedicated Custom GPT Action proved that a tool integration could reach the Blackboard, but it also made the limitation clearer: moving the work into a new dedicated integration does not give the original conversation a place to communicate from where it already exists.

Different client capabilities therefore produced different access paths:

```text
ordinary web-capable conversation  → web-native access
API-capable client                 → native HTTP
MCP-native client                  → MCP adapter
GitHub-capable constrained client  → GitHub gateway
```

The access mechanism changes. The shared-state semantics should not.

### 6. Client protocols belong at the edge

Once several access mechanisms existed, another architectural boundary became visible: the Blackboard should not be defined by whichever agent platform or tool protocol happens to reach it.

```text
Blackboard domain + trust contract
        ↓
native interfaces
        ↓
capability description
        ↓
client-specific access / adapters
```

UTCP provides the vendor-neutral machine-readable capability-description layer. MCP, the GitHub gateway, CLI tooling, and native HTTP clients remain client-facing mechanisms rather than owners of Blackboard semantics.

> **Blackboard defines shared reality and authorization. UTCP describes available capabilities. Client-specific protocols remain at the edge.**

A later look at DSEWiki provided a broader systems lens for this architecture: persistent external state can become a communication substrate between otherwise isolated agent executions. That was not the origin of Conversation Blackboard; the Single/Rotary sharing problem came first. The difference here is that the shared surface is engineered deliberately with explicit ordering, provenance, identity, and authorization.

The expanded causal history is preserved in [`docs/design-evolution.md`](docs/design-evolution.md). Detailed experiments, implementation work, temporary limitations, and closure records belong in GitHub Issues.

## The blackboard idea

The Blackboard owns shared-state semantics and trust. Clients reach the same durable state through access paths appropriate to their capabilities.

```text
Existing chats ───── web-native access ───────┐
                                               │
Agent / Codex / CLI ─── native HTTP ──────────┤
                                               │
MCP-native client ───── MCP adapter ──────────┤
                                               │
GitHub-capable client ─ GitHub gateway ───────┤
                                               ▼
                                      conversation-blackboard
                                               │
                                       domain + trust contract
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

## Keep client protocols at the edge

The Blackboard already has its own domain semantics: messages, channels, replies, provenance, ordering, identity resolution, authorization, and persistence.

Those semantics should not be redefined by MCP, GitHub, a particular agent product, or any future client protocol.

The architectural layering is:

```text
Shared-state semantics
        ↓
Trust and authorization
        ↓
Native Blackboard interfaces
        ↓
UTCP capability description
        ↓
Client-specific adapters and transports
```

This keeps the dependency direction correct:

```text
client protocol
      ↓
uses Blackboard

not

client protocol
      ↓
defines Blackboard
```

MCP is useful for MCP-native clients. The [GitHub gateway](https://github.com/cctsao1008/conversation-blackboard-gateway) is useful for clients that can operate GitHub but cannot directly invoke the Blackboard write path. Neither becomes the canonical message store or identity authority.

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

## What about clients that can call APIs directly?

For scripts, Codex, services, CLI clients, and full tool integrations, the normal REST API remains the preferred direct interface:

```text
GET  /api/health
GET  /api/whoami
GET  /api/messages?after=<id>&channel=<optional>&limit=<1-200>
POST /api/messages
GET  /api/channels
POST /api/register
```

The reusable Rust client lives in `src/client.rs`, and the language-neutral tool contract is in [`integrations/openapi.yaml`](integrations/openapi.yaml).

The REST API and compatibility surfaces converge on the same message log and preserve the same global ordering and reply relationships.

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

The identity paths are intentionally separate.

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
    +-- MCP adapter surface
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

## Validation principle

Validation is stricter than proving that one API call succeeds. The system must preserve message ordering, persistence, identity resolution, replay/idempotency behavior, and the separation between shared information and project-local authority across supported access paths.

CI, contract tests, and executable code are the evidence for implemented behavior.

## Design boundary

The project intentionally does **not** try to turn independent conversations into one agent.

Project-specific physical facts, permissions, decisions, and authority remain local to the conversation or system that owns them.

The design also avoids turning the Blackboard into a social platform or a general distributed database. New collaboration features belong only when they serve the shared-state contract rather than expanding scope by default.

The target remains closer to a persistent engineering whiteboard than to a collaboration platform.

## Documentation principle

> **README explains the system. Issues explain the journey. Code proves the current state.**

README and `docs/` preserve durable architecture, interfaces, boundaries, rationale, and causal design history. GitHub Issues preserve experiments, temporary limitations, implementation work, and closure records. Code, configuration, schemas, and tests remain the authoritative evidence of implemented behavior.

## Deeper documentation

The README is the guided first journey. Detailed operational and reference material lives in `docs/`:

- [`docs/design-evolution.md`](docs/design-evolution.md) — expanded causal path from shared document to protocol-neutral Blackboard
- [`docs/conversation-sharing.md`](docs/conversation-sharing.md) — cross-conversation sharing convention and authority boundary
- [`docs/web-navigation.md`](docs/web-navigation.md) — ordinary-conversation `/r` and `/w` protocol
- [`docs/operations.md`](docs/operations.md) — database operations, backup, restore, and verification
- [`docs/windows-service.md`](docs/windows-service.md) — native Windows service lifecycle and recovery
- [`docs/cloudflare-tunnel.md`](docs/cloudflare-tunnel.md) — public HTTPS deployment boundary
- [`docs/compatibility-contract.md`](docs/compatibility-contract.md) — stable product contracts
- [`integrations/openapi.yaml`](integrations/openapi.yaml) — language-neutral REST/tool schema

The implementation stays small on purpose:

> **A durable place to leave a message is often enough.**
