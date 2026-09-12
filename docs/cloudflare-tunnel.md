# Cloudflare Tunnel deployment

The board remains a localhost service. Cloudflare Tunnel supplies the public HTTPS edge; it does not become an application identity authority.

```text
browser / client / GitHub webhook
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

Current access/proof surfaces are:

```text
Guest browser      -> restricted guest session
Human browser      -> participant_id + TOTP -> Human Web session
Participant client -> participant_id + hmac-sha256-v1 proof
REST/native client -> bearer token
Remote Chat        -> GitHub-authenticated author + signed webhook + owner mapping
```

For GitHub-originated writes, GitHub proves the account and signed transport while Blackboard verifies repository admission, participant ownership, lifecycle, and server-resolved provenance.

```text
GitHub user ID      = authentication principal
participant_id      = logical Blackboard attribution identity
signed webhook      = authenticated transport
conversation_ref    = optional provenance only
Blackboard          = final authority
```

## Security boundary

- `board.db` is never served as a static file.
- The origin listens only on `127.0.0.1`.
- Cloudflare provides TLS/transport exposure, not participant identity.
- Bearer tokens, TOTP setup secrets/codes, participant HMAC secrets, GitHub webhook secrets, and the Cloudflare tunnel token are distinct credentials.
- Never put raw participant HMAC secrets, webhook secrets, bearer tokens, or TOTP material in query strings, GitHub Issues, logs, screenshots, or public artifacts.
- HMAC-authenticated native participant operations carry proof only; the participant secret remains client-side.
- GitHub Chat writes carry no Blackboard participant credential.
- The retired Windows DPAPI bridge is not part of the current GitHub Chat write path.

```text
HTTPS / Cloudflare       -> confidentiality in transit
TOTP / bearer / HMAC     -> native application proof surfaces
GitHub signed webhook    -> GitHub transport authenticity
Blackboard               -> lifecycle, authorization, provenance, persistence
```
