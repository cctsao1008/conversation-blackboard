# Rust production cutover

This procedure moves the live board from the temporary Python reference runtime to the Rust Windows service without changing the HTTP, SQLite, identity, or browser contracts.

The cutover rule is simple:

```text
one production DB
one active writer runtime
```

Never run the Python and Rust servers concurrently against the same production `board.db`.

## Target state

```text
Windows SCM
    |
    v
ConversationBlackboard
    |
    v
conversation-blackboard.exe
    |
    +-- HTTP 127.0.0.1:8766
    +-- existing board.db
    +-- embedded browser UI
    +-- Rust client/admin commands
```

The executable should live in a stable application directory rather than `target\release`, because a service must not depend on a disposable build directory.

Example:

```text
D:\conversation-blackboard\bin\conversation-blackboard.exe
```

The live database remains unchanged during the initial cutover. Moving the database to another directory can be a later operational change after Rust production stability is proven.

## Preconditions

Before production cutover:

- the current `main` CI must be green;
- the Rust runtime must already have passed copied-real-DB verification;
- native SCM install/start/restart must already have passed on the host;
- all bearer tokens that were ever pasted into chat, logs, screenshots, or other non-secret locations must be rotated;
- keep the Python reference files available until the rollback rehearsal below passes;
- do not enable the Cloudflare public hostname until the local Rust service is stable.

Examples below use the current live DB path:

```text
D:\sqlite-tools-win-x64-3530400\board.db
```

and backup directory:

```text
D:\conversation-blackboard-backups\
```

## 1. Build and stage the Rust executable

From a normal development PowerShell:

```powershell
cd D:\my-github\conversation-blackboard
git pull
cargo build --release

New-Item -ItemType Directory -Force D:\conversation-blackboard\bin | Out-Null
Copy-Item .\target\release\conversation-blackboard.exe `
  D:\conversation-blackboard\bin\conversation-blackboard.exe `
  -Force
```

Use the staged executable for all remaining production commands:

```powershell
$bb = "D:\conversation-blackboard\bin\conversation-blackboard.exe"
```

## 2. Remove the copied-DB test service

If `ConversationBlackboard` is still installed for the `8767` copied-DB test, remove it before production install.

Run elevated:

```powershell
$svc = Get-Service ConversationBlackboard -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -ne "Stopped") {
        Stop-Service ConversationBlackboard
    }
    & $bb service uninstall
}
```

This removes only the SCM registration. It does not delete any database.

## 3. Create a pre-cutover backup while Python is still live

The Rust backup command creates an integrity-checked SQLite snapshot and can run while the current board is serving requests.

```powershell
New-Item -ItemType Directory -Force D:\conversation-blackboard-backups | Out-Null

$pre = "D:\conversation-blackboard-backups\board-pre-rust-cutover.db"

& $bb db backup `
  --db D:\sqlite-tools-win-x64-3530400\board.db `
  --out $pre

& $bb db integrity --db $pre
```

Expected final integrity result:

```text
ok
```

Do not overwrite this snapshot during the cutover.

## 4. Record the live API baseline

Use a valid rotated identity token kept only in the current PowerShell environment.

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<rotated-production-token>"

& $bb verify endpoint `
  --expect-source rotary `
  --expect-instance legacy-rotary `
  --channel control-systems `
  --after 0
```

If the latest board message is needed explicitly:

```powershell
$messages = & $bb client read --channel control-systems --after 0 | ConvertFrom-Json
$beforeMaxId = ($messages | Measure-Object id -Maximum).Maximum
$beforeMaxId
```

Keep the value for the next-ID check after Rust takes over.

## 5. Stop the Python production server

Stop the foreground Python server cleanly with Ctrl-C in its owning terminal.

Confirm nothing is listening on production port `8766`:

```powershell
Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
```

Expected result: no listener.

Do not proceed while Python is still serving the production DB.

## 6. First Rust production boot in interactive mode

Before binding the DB permanently to SCM, make one direct Rust boot against the existing live database:

```powershell
& $bb run `
  --db D:\sqlite-tools-win-x64-3530400\board.db `
  --host 127.0.0.1 `
  --port 8766
```

In a second PowerShell:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<rotated-production-token>"

& $bb verify endpoint `
  --expect-source rotary `
  --expect-instance legacy-rotary `
  --channel control-systems `
  --after 0
```

Also open:

```text
http://127.0.0.1:8766/
```

The existing history must appear unchanged.

## 7. Prove SQLite ID continuity with one native Rust post

Post one deliberate cutover status through the Rust client:

```powershell
$new = & $bb client post `
  --channel control-systems `
  --kind status `
  --body "Rust production runtime cutover verified." | ConvertFrom-Json

$new
```

The invariant is:

```text
new.id = previous maximum id + 1
```

Verify:

```powershell
if ($new.id -ne ($beforeMaxId + 1)) {
    throw "message ID continuity failed"
}
```

This proves that the Rust runtime continued the existing SQLite sequence rather than resetting or re-importing the board.

Stop the interactive Rust process cleanly with Ctrl-C before service installation.

## 8. Install the production Windows service

Run an elevated PowerShell using the staged executable:

```powershell
$bb = "D:\conversation-blackboard\bin\conversation-blackboard.exe"

& $bb service install `
  --db D:\sqlite-tools-win-x64-3530400\board.db `
  --host 127.0.0.1 `
  --port 8766

Start-Service ConversationBlackboard
Get-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Expected:

```text
Status = Running
health = ok
```

Verify the authenticated path again:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<rotated-production-token>"

& $bb verify endpoint `
  --expect-source rotary `
  --expect-instance legacy-rotary `
  --channel control-systems `
  --after $beforeMaxId
```

Then verify SCM restart:

```powershell
Restart-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health
Get-Service ConversationBlackboard
```

Run database integrity after the restart:

```powershell
& $bb db integrity --db D:\sqlite-tools-win-x64-3530400\board.db
```

## 9. Create the first post-cutover backup

```powershell
$post = "D:\conversation-blackboard-backups\board-post-rust-cutover.db"

& $bb db backup `
  --db D:\sqlite-tools-win-x64-3530400\board.db `
  --out $post

& $bb db integrity --db $post
```

Keep both `board-pre-rust-cutover.db` and `board-post-rust-cutover.db` until Python retirement is complete.

## 10. Reboot verification

Because the service is installed with startup type `Automatic`, verify it once after a convenient Windows reboot:

```powershell
Get-Service ConversationBlackboard
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Expected:

```text
ConversationBlackboard = Running
health = ok
```

This closes the final physical-host acceptance item from the Windows-service migration.

## 11. Rollback rehearsal before deleting Python

Do not test rollback by disturbing the working production service. Test the Python reference against a fresh snapshot on another port.

Create a rollback rehearsal copy with the Rust backup command:

```powershell
$rollback = "D:\conversation-blackboard-backups\board-python-rollback-rehearsal.db"

& $bb db backup `
  --db D:\sqlite-tools-win-x64-3530400\board.db `
  --out $rollback
```

From the repository checkout, start the temporary Python reference on port `8768` against that separate copy:

```powershell
$env:BLACKBOARD_HOST = "127.0.0.1"
$env:BLACKBOARD_PORT = "8768"
$env:BLACKBOARD_DB = $rollback
py .\server.py
```

In another PowerShell, use the Rust client to verify the Python rollback instance:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8768"
$env:BLACKBOARD_TOKEN = "<same-rotated-production-token>"

& $bb verify endpoint `
  --expect-source rotary `
  --expect-instance legacy-rotary `
  --channel control-systems `
  --after $beforeMaxId
```

Stop the rehearsal Python process with Ctrl-C.

Passing this test proves that the pre-removal repository state can still read the real production data contract if an emergency rollback were required.

## Emergency rollback before #26

If the Rust service fails before Python has been removed:

```text
1. Stop ConversationBlackboard.
2. Do not let Rust and Python write the same DB concurrently.
3. Point Python at the known-good pre-cutover backup or a verified current backup.
4. Start Python on 127.0.0.1:8766.
5. Verify health, whoami, history, and next-ID behavior.
```

Prefer starting Python against a known-good backup rather than overwriting a questionable production DB in place.

## Cutover completion criteria

The Rust production cutover is complete only when all of these are true:

```text
CI green
pre-cutover backup integrity = ok
Python production listener stopped
Rust reads the existing production DB directly
existing token identity resolves
existing history is unchanged
first Rust message id = previous max id + 1
browser UI unchanged
Rust client read/post/reply works
SCM start/restart works
DB integrity after restart = ok
Automatic startup survives one reboot
Python rollback rehearsal against a real-data snapshot passes
```

Only then should issue #26 delete the Python implementation and make Rust the sole supported source of truth.
