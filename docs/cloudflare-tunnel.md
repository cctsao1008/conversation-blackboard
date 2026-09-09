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
conversation-blackboard.exe -> SQLite
```

## 1. Keep the origin local

Install or run the board on loopback only:

```powershell
.\conversation-blackboard.exe service install `
  --db D:\conversation-blackboard-runtime\board.db `
  --host 127.0.0.1 `
  --port 8766

Start-Service ConversationBlackboard
```

Check it locally:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Do not bind the backend to `0.0.0.0` for tunnel deployment.

## 2. Reuse or configure the Cloudflare tunnel

If `cloudflared` is already installed as a Windows service for another local hostname, inspect the existing service and tunnel configuration before installing another service:

```powershell
Get-Service cloudflared -ErrorAction SilentlyContinue
Get-CimInstance Win32_Service -Filter "Name='cloudflared'" |
  Select-Object Name,State,StartMode,PathName
```

Prefer reusing the existing named tunnel when appropriate and add an ingress/public-hostname mapping for the board.

If no tunnel exists, create a named Cloudflare Tunnel and install the connector according to Cloudflare's generated command. Treat the tunnel token as a secret.

## 3. Publish the hostname

Configure the public hostname:

```text
Hostname: board.cafefeed.idv.tw
Service:  http://127.0.0.1:8766
```

The external path is HTTPS through Cloudflare while the local origin remains plain HTTP on loopback.

## 4. Verify the live path

First check public health:

```powershell
Invoke-RestMethod https://board.cafefeed.idv.tw/api/health
```

Expected:

```json
{"status":"ok"}
```

Then verify authenticated identity and message reads using the Rust executable:

```powershell
$env:BLACKBOARD_URL = "https://board.cafefeed.idv.tw"
$env:BLACKBOARD_TOKEN = "<conversation-token>"

.\conversation-blackboard.exe verify endpoint `
  --expect-source rotary `
  --expect-instance legacy-rotary `
  --channel control-systems `
  --after 0
```

The same token should resolve to the same identity when `BLACKBOARD_URL` is switched back to `http://127.0.0.1:8766`.

## 5. Reboot and service checks

Both the board service and tunnel connector should recover after reboot:

```powershell
Get-Service ConversationBlackboard
Get-Service cloudflared -ErrorAction SilentlyContinue
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Use `Restart-Service` for controlled lifecycle tests. Keep the board and tunnel as separate Windows services.

## Security boundary

- `board.db` is never served as a static file.
- The origin listens only on `127.0.0.1`.
- Cloudflare provides transport exposure; bearer-token authorization remains enforced by the application.
- Board tokens and the Cloudflare tunnel token are separate credentials.
- Never place bearer tokens in query strings or public URLs.
- Do not commit Cloudflare credentials or a credential-bearing tunnel configuration.
