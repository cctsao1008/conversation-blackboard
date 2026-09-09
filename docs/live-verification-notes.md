# Live verification notes

For verification against an active SQLite database in WAL mode, use SQLite's online backup API rather than copying only the main `board.db` file.

A plain filesystem copy can be internally valid while omitting committed state that still resides in `board.db-wal`, including recent messages, identities, or rotated token hashes.

For current Windows verification, prefer:

```powershell
& D:\sqlite-tools-win-x64-3530400\sqlite3.exe `
  D:\sqlite-tools-win-x64-3530400\board.db `
  ".backup 'D:/conversation-blackboard-runtime/board-service-test.db'"
```

or the repository backup helper during the Python-to-Rust migration period.

Do not paste bearer tokens into chat, issues, or logs. Rotate any token that has been exposed outside its intended local secret store.
