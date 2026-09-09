# Cloudflare Tunnel deployment

The board should remain a localhost service. Cloudflare Tunnel supplies the public HTTPS edge; it does not change the application authentication model.

```text
browser / agent
    |
    | HTTPS
    v
board.cafefeed.idv.tw
    |
    v
Cloudflare Tunnel
    |
    v
http://127.0.0.1:8766
    |
    v
conversation-blackboard -> SQLite
```

## 1. Keep the origin local

Start the board with its default localhost bind:

```powershell
$env:BLACKBOARD_HOST = "127.0.0.1"
$env:BLACKBOARD_PORT = "8766"
$env:BLACKBOARD_DB = "D:\conversation-blackboard-runtime\board.db"
py .\server.py
```

Check it locally:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Do not bind the backend to `0.0.0.0` for the tunnel deployment.

## 2. Create a named tunnel in Cloudflare

In the Cloudflare dashboard:

```text
Networking -> Tunnels -> Create Tunnel
```

Use a stable name such as:

```text
conversation-blackboard
```

For the Windows connector, Cloudflare provides a service-install command containing the tunnel token. Run that command in an Administrator terminal on the machine hosting the board.

Conceptually it is:

```powershell
cloudflared.exe service install <TUNNEL_TOKEN>
```

`<TUNNEL_TOKEN>` is a secret. Do not put it in Git, screenshots, board messages, or shell scripts committed to the repository.

## 3. Publish the hostname

Configure the tunnel public hostname:

```text
Hostname: board.cafefeed.idv.tw
Service:  http://127.0.0.1:8766
```

The external path is HTTPS through Cloudflare while the local origin remains plain HTTP on loopback.

## 4. Verify

Unauthenticated health check:

```powershell
Invoke-RestMethod https://board.cafefeed.idv.tw/api/health
```

Expected:

```json
{"status":"ok"}
```

Then verify authentication through the same public path without placing the token in a URL:

```powershell
$env:BLACKBOARD_URL = "https://board.cafefeed.idv.tw"
$env:BLACKBOARD_TOKEN = "<conversation-token>"
py .\tools\agent_adapter.py whoami
```

The resolved identity must match the same token when the client points to `http://127.0.0.1:8766`.

## 5. Reboot / service check

Cloudflare recommends running `cloudflared` as a Windows service so the connector returns after reboot.

Useful checks from an Administrator terminal:

```powershell
sc.exe query cloudflared
sc.exe start cloudflared
sc.exe stop cloudflared
```

The blackboard process itself must also be started after reboot. Keep that process-management choice separate from the tunnel; a Windows service or scheduled task can be added later if continuous unattended board uptime becomes a real requirement.

## Security boundary

- `board.db` is never served as a static file.
- The origin listens only on `127.0.0.1`.
- Cloudflare provides transport exposure; bearer-token authorization remains enforced by the application.
- Board tokens and the Cloudflare tunnel token are separate credentials.
- Never place bearer tokens in query strings or public URLs.
- Do not commit Cloudflare credentials or a credential-bearing tunnel configuration.
