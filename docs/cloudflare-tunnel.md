# Cloudflare Tunnel deployment

The board remains a localhost service. Cloudflare Tunnel supplies the public HTTPS edge; it does not become an application identity authority.

```text
browser / client
    | HTTPS
    v
public hostname
    |
    v
Cloudflare Tunnel
    |
    v
http://127.0.0.1:8766
    |
    v
conversation-blackboard.exe -> SQLite
```

## Keep the origin local

Run the service on loopback and verify it locally through `/api/health`. Do not bind the backend to `0.0.0.0` merely because a tunnel is used.

Reuse an existing named tunnel when appropriate. Treat the Cloudflare tunnel token as a transport secret and keep it out of the repository and command logs.

## Application authentication remains Blackboard-owned

Cloudflare does not replace application authentication.

Current credential/proof surfaces are:

```text
Guest browser      -> restricted guest session
Human browser      -> participant_id + TOTP -> Human Web session
Participant client -> participant_id + hmac-sha256-v1 proof
REST/native client -> bearer token
```

Conversation Blackboard verifies these after the request traverses Cloudflare and continues to resolve authoritative provenance itself.

## Security boundary

- `board.db` is never served as a static file.
- The origin listens only on `127.0.0.1`.
- Cloudflare provides TLS/transport exposure, not participant identity.
- Bearer tokens, TOTP setup secrets/codes, participant HMAC secrets, DPAPI credential files, and the Cloudflare tunnel token are distinct credentials.
- Never put raw participant HMAC secrets in query strings, GitHub Issues, logs, screenshots, or public artifacts.
- HMAC-authenticated operations carry proof only; the participant secret remains client-side/local.
- A local DPAPI credential represents local signing capability, not central authority.

```text
HTTPS / Cloudflare -> confidentiality in transit
TOTP / bearer / HMAC -> application proof
Blackboard -> lifecycle, authorization, provenance, persistence
```
