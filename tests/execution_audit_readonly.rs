use std::{fs, process::Command};

use rusqlite::{Connection, OpenFlags};
use tempfile::tempdir;

fn run(exe: &str, args: &[&str], db: &std::path::Path) -> std::process::Output {
    let mut command = Command::new(exe);
    command.args(args).arg(db);
    command.output().unwrap()
}

fn journal_mode(path: &std::path::Path) -> String {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    conn.query_row("PRAGMA journal_mode", [], |row| row.get::<_, String>(0))
        .unwrap()
}

#[test]
fn execution_audit_rejects_legacy_schema_without_mutating_database() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("board.db");
    let exe = env!("CARGO_BIN_EXE_conversation-blackboard");

    let init = run(exe, &["db", "init", "--db"], &db);
    assert!(
        init.status.success(),
        "initial db init failed: {}",
        String::from_utf8_lossy(&init.stderr)
    );

    // Downgrade only ingress provenance to the pre-#87 shape and deliberately
    // switch away from WAL. A writable `db::connect()` in the audit path would
    // change this database before the schema preflight could reject it.
    let conn = Connection::open(&db).unwrap();
    conn.execute_batch(
        "DROP INDEX IF EXISTS idx_ingress_provenance_execution;
         DROP INDEX IF EXISTS idx_ingress_provenance_intent;
         DROP TABLE ingress_provenance;
         CREATE TABLE ingress_provenance (
             delivery_id         TEXT PRIMARY KEY,
             intent_id           TEXT NOT NULL,
             transport           TEXT NOT NULL,
             external_ref        TEXT NOT NULL,
             principal_provider  TEXT NOT NULL,
             principal_subject   TEXT NOT NULL,
             created_at          INTEGER NOT NULL DEFAULT (unixepoch())
         );
         CREATE INDEX idx_ingress_provenance_intent
             ON ingress_provenance(intent_id);
         PRAGMA journal_mode = DELETE;",
    )
    .unwrap();
    drop(conn);

    assert_eq!(journal_mode(&db), "delete");
    let before = fs::read(&db).unwrap();

    let audit = Command::new(exe)
        .args(["execution", "audit", "--db"])
        .arg(&db)
        .args([
            "--participant-id",
            "maker-main",
            "--intent-id",
            "intent-legacy",
        ])
        .output()
        .unwrap();
    assert!(!audit.status.success());
    let stderr = String::from_utf8_lossy(&audit.stderr);
    assert!(
        stderr.contains("execution schema requires migration"),
        "unexpected audit error: {stderr}"
    );

    let after = fs::read(&db).unwrap();
    assert_eq!(after, before, "audit command changed database bytes");
    assert_eq!(journal_mode(&db), "delete");

    // Migration is an explicit operator action. After db init, the same audit
    // reaches the semantic lookup rather than silently migrating during read.
    let migrate = run(exe, &["db", "init", "--db"], &db);
    assert!(
        migrate.status.success(),
        "explicit migration failed: {}",
        String::from_utf8_lossy(&migrate.stderr)
    );

    let post_migration_audit = Command::new(exe)
        .args(["execution", "audit", "--db"])
        .arg(&db)
        .args([
            "--participant-id",
            "maker-main",
            "--intent-id",
            "intent-legacy",
        ])
        .output()
        .unwrap();
    assert!(!post_migration_audit.status.success());
    let stderr = String::from_utf8_lossy(&post_migration_audit.stderr);
    assert!(
        stderr.contains("execution not found"),
        "unexpected error: {stderr}"
    );
    assert!(!stderr.contains("requires migration"));
}
