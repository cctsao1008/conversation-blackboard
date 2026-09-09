# Live verification notes

For verification against an active SQLite database in WAL mode, use the application's SQLite-aware backup command rather than copying only the main `board.db` file.

A plain filesystem copy can be internally valid while omitting committed state that still resides in `board.db-wal`, including recent messages, identities, or rotated token hashes.

Prefer:

```powershell
.\conversation-blackboard.exe db backup `
  --db D:\conversation-blackboard-runtime\board.db `
  --out D:\conversation-blackboard-backups\board-verification.db

.\conversation-blackboard.exe db integrity `
  --db D:\conversation-blackboard-backups\board-verification.db
```

For direct verification against the live endpoint, keep credentials in environment variables:

```powershell
$env:BLACKBOARD_URL = "http://127.0.0.1:8766"
$env:BLACKBOARD_TOKEN = "<conversation-token>"

.\conversation-blackboard.exe verify endpoint --after 0
```

Do not paste bearer tokens into chat, issues, screenshots, or logs. Rotate any token that has been exposed outside its intended local secret store.
