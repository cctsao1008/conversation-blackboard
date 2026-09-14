from pathlib import Path

p = Path("src/execution_audit.rs")
s = p.read_text(encoding="utf-8")
if "fn verify_all_cli_accepts_clean_current_schema" in s:
    raise SystemExit("CLI regression tests already present")

s += r'''

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use tempfile::tempdir;

    #[test]
    fn verify_all_cli_accepts_clean_current_schema() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();

        dispatch(ExecutionCommand::VerifyAll { db: path }).unwrap();
    }

    #[test]
    fn verify_all_cli_fails_for_invalid_sweep_state() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        conn.execute(
            "INSERT INTO navigation_writes (instance, nonce, request_hash, message_id)
             VALUES ('ghost-main', 'orphan-intent', 'hash', 99)",
            [],
        )
        .unwrap();
        drop(conn);

        let result = dispatch(ExecutionCommand::VerifyAll { db: path });
        let error = result.expect_err("invalid sweep must fail").to_string();
        assert!(error.contains("execution audit sweep verification failed"));
    }

    #[test]
    fn verify_all_cli_refuses_unmigrated_schema_without_mutation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("legacy.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                source TEXT NOT NULL,
                instance TEXT NOT NULL,
                kind TEXT NOT NULL,
                body TEXT NOT NULL
            );",
        )
        .unwrap();
        let before_schema: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        drop(conn);

        let result = dispatch(ExecutionCommand::VerifyAll { db: path.clone() });
        let error = result.expect_err("unmigrated schema must fail").to_string();
        assert!(error.contains("execution schema requires migration"));

        let conn = Connection::open(&path).unwrap();
        let after_schema: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before_schema, after_schema);
        let execution_receipts_exists: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'table' AND name = 'execution_receipts'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(execution_receipts_exists, 0);
    }
}
'''

p.write_text(s, encoding="utf-8")
