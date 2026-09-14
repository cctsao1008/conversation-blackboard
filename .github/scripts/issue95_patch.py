from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing marker: {label}")
    return text.replace(old, new, 1)

# --- schema.sql: make fresh installs current ---
p = Path("schema.sql")
s = p.read_text(encoding="utf-8")
old = '''CREATE TABLE IF NOT EXISTS ingress_provenance (\n    delivery_id         TEXT PRIMARY KEY,\n    intent_id           TEXT NOT NULL,\n    transport           TEXT NOT NULL,\n    external_ref        TEXT NOT NULL,\n    principal_provider  TEXT NOT NULL,\n    principal_subject   TEXT NOT NULL,\n    created_at          INTEGER NOT NULL DEFAULT (unixepoch())\n);\n\nCREATE INDEX IF NOT EXISTS idx_ingress_provenance_intent\nON ingress_provenance(intent_id);\n\nCREATE TABLE IF NOT EXISTS execution_receipts (\n'''
new = '''CREATE TABLE IF NOT EXISTS ingress_provenance (\n    delivery_id         TEXT PRIMARY KEY,\n    participant_id      TEXT,\n    intent_id           TEXT NOT NULL,\n    transport           TEXT NOT NULL,\n    external_ref        TEXT NOT NULL,\n    principal_provider  TEXT NOT NULL,\n    principal_subject   TEXT NOT NULL,\n    created_at          INTEGER NOT NULL DEFAULT (unixepoch())\n);\n\nCREATE INDEX IF NOT EXISTS idx_ingress_provenance_intent\nON ingress_provenance(intent_id);\n\nCREATE INDEX IF NOT EXISTS idx_ingress_provenance_execution\nON ingress_provenance(participant_id, intent_id);\n\nCREATE TABLE IF NOT EXISTS execution_receipts (\n'''
s = replace_once(s, old, new, "fresh ingress schema")
receipt_tail = '''CREATE INDEX IF NOT EXISTS idx_execution_receipts_message\nON execution_receipts(message_id);'''
auth_table = '''CREATE INDEX IF NOT EXISTS idx_execution_receipts_message\nON execution_receipts(message_id);\n\nCREATE TABLE IF NOT EXISTS execution_authorization_provenance (\n    participant_id      TEXT NOT NULL,\n    intent_id           TEXT NOT NULL,\n    principal_provider  TEXT,\n    principal_subject   TEXT,\n    capability          TEXT,\n    resource            TEXT,\n    source              TEXT NOT NULL,\n    reason              TEXT NOT NULL,\n    grant_id            INTEGER,\n    created_at          INTEGER NOT NULL DEFAULT (unixepoch()),\n    PRIMARY KEY (participant_id, intent_id)\n);'''
s = replace_once(s, receipt_tail, auth_table, "fresh authorization provenance schema")
p.write_text(s, encoding="utf-8")

# --- execution.rs: migration is explicit; canonical reads are observational ---
p = Path("src/execution.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "pub fn ensure_execution_tables(conn: &Connection) -> rusqlite::Result<()> {",
    "pub fn migrate_execution_schema(conn: &Connection) -> rusqlite::Result<()> {",
    "rename execution migration",
)

for signature in [
    '''pub fn get_execution_receipt(\n    conn: &Connection,\n    participant_id: &str,\n    intent_id: &str,\n) -> rusqlite::Result<Option<ExecutionReceipt>> {\n    ensure_execution_tables(conn)?;\n''',
    '''pub fn get_authorization_provenance(\n    conn: &Connection,\n    participant_id: &str,\n    intent_id: &str,\n) -> rusqlite::Result<Option<AuthorizationProvenance>> {\n    ensure_execution_tables(conn)?;\n''',
    '''pub fn verify_execution_audit_integrity(\n    conn: &Connection,\n    participant_id: &str,\n    intent_id: &str,\n) -> rusqlite::Result<ExecutionAuditIntegrityReport> {\n    ensure_execution_tables(conn)?;\n''',
    '''pub fn execute_message_intent(\n    conn: &Connection,\n    identity: &Identity,\n    request: MessageExecutionRequest<'_>,\n) -> rusqlite::Result<MessageExecutionResult> {\n    ensure_execution_tables(conn)?;\n''',
]:
    if signature not in s:
        raise SystemExit("missing read/write migration call marker")
    s = s.replace(signature, signature.replace("    ensure_execution_tables(conn)?;\n", ""), 1)

# Keep the test-only low-level recorder self-contained, but make its migration
# call explicit by name.
s = s.replace("ensure_execution_tables(conn)?;", "migrate_execution_schema(conn)?;")
s = s.replace("ensure_execution_tables(&conn).unwrap();", "migrate_execution_schema(&conn).unwrap();")

marker = '''pub fn get_execution_receipt(\n'''
helper = r'''pub fn execution_schema_current(conn: &Connection) -> rusqlite::Result<bool> {
    fn table_exists(conn: &Connection, table: &str) -> rusqlite::Result<bool> {
        Ok(conn
            .query_row(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
                [table],
                |_| Ok(1_i64),
            )
            .optional()?
            .is_some())
    }

    fn has_column(conn: &Connection, table: &str, column: &str) -> rusqlite::Result<bool> {
        if !table_exists(conn, table)? {
            return Ok(false);
        }
        let sql = format!("PRAGMA table_info({table})");
        let mut stmt = conn.prepare(&sql)?;
        let columns = stmt
            .query_map([], |row| row.get::<_, String>(1))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(columns.iter().any(|name| name == column))
    }

    if !table_exists(conn, "execution_receipts")?
        || !has_column(conn, "ingress_provenance", "participant_id")?
        || !table_exists(conn, "execution_authorization_provenance")?
    {
        return Ok(false);
    }

    for column in [
        "principal_provider",
        "principal_subject",
        "capability",
        "resource",
        "source",
        "reason",
        "grant_id",
    ] {
        if !has_column(conn, "execution_authorization_provenance", column)? {
            return Ok(false);
        }
    }
    Ok(true)
}

'''
s = replace_once(s, marker, helper + marker, "schema current helper")
p.write_text(s, encoding="utf-8")

# --- db.rs: initialization owns execution migration ---
p = Path("src/db.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "use crate::model::{ChannelMetadata, ChannelSummary, Identity, Message};",
    "use crate::{execution, model::{ChannelMetadata, ChannelSummary, Identity, Message}};",
    "db execution import",
)
s = replace_once(
    s,
    '''    migrate_channel_metadata(&conn)?;\n    Ok(())\n}''',
    '''    migrate_channel_metadata(&conn)?;\n    execution::migrate_execution_schema(&conn)?;\n    Ok(())\n}''',
    "db initialize migration",
)
p.write_text(s, encoding="utf-8")

# --- execution_audit.rs: audit CLI refuses hidden migration ---
p = Path("src/execution_audit.rs")
s = p.read_text(encoding="utf-8")
s = s.replace(
    "            let conn = db::connect(&path)?;\n            let Some(audit) =",
    '''            let conn = db::connect(&path)?;\n            require_current_execution_schema(&conn, &path)?;\n            let Some(audit) =''',
    1,
)
s = s.replace(
    "            let conn = db::connect(&path)?;\n            let report =",
    '''            let conn = db::connect(&path)?;\n            require_current_execution_schema(&conn, &path)?;\n            let report =''',
    1,
)
marker = '''fn require_database(path: &Path) -> DynResult {\n'''
helper = r'''fn require_current_execution_schema(conn: &rusqlite::Connection, path: &Path) -> DynResult {
    if execution::execution_schema_current(conn)? {
        return Ok(());
    }
    Err(format!(
        "execution schema requires migration: run `conversation-blackboard db init --db {}` before audit",
        path.display()
    )
    .into())
}

'''
s = replace_once(s, marker, helper + marker, "CLI schema preflight")
p.write_text(s, encoding="utf-8")

# --- existing tests: rename explicit migration setup calls ---
for path in [Path("src/execution_integrity_tests.rs")]:
    s = path.read_text(encoding="utf-8")
    s = s.replace("execution::ensure_execution_tables", "execution::migrate_execution_schema")
    path.write_text(s, encoding="utf-8")

# --- strengthen integrity/migration regressions ---
p = Path("src/execution_integrity_tests.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "use crate::execution;",
    "use crate::{db, execution};\nuse tempfile::tempdir;",
    "integrity test imports",
)
marker = '''#[test]\nfn valid_committed_audit_passes_integrity_verification() {\n'''
tests = r'''#[test]
fn db_initialize_produces_current_execution_schema() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("board.db");
    db::initialize(&path).unwrap();
    let conn = db::connect(&path).unwrap();
    assert!(execution::execution_schema_current(&conn).unwrap());

    let ingress_columns = conn
        .prepare("PRAGMA table_info(ingress_provenance)")
        .unwrap()
        .query_map([], |row| row.get::<_, String>(1))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    assert!(ingress_columns.iter().any(|name| name == "participant_id"));

    let auth_columns = conn
        .prepare("PRAGMA table_info(execution_authorization_provenance)")
        .unwrap()
        .query_map([], |row| row.get::<_, String>(1))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    for required in [
        "principal_provider",
        "principal_subject",
        "capability",
        "resource",
        "source",
        "reason",
        "grant_id",
    ] {
        assert!(auth_columns.iter().any(|name| name == required));
    }
}

#[test]
fn execution_reads_do_not_backfill_legacy_evidence() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE ingress_provenance SET participant_id = NULL WHERE delivery_id = 'delivery-85'",
        [],
    )
    .unwrap();
    conn.execute(
        "UPDATE execution_authorization_provenance SET capability = NULL
         WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
        [],
    )
    .unwrap();
    let before = conn.total_changes();

    let _ = execution::get_execution_receipt(&conn, "maker-main", "intent-85").unwrap();
    let auth = execution::get_authorization_provenance(&conn, "maker-main", "intent-85")
        .unwrap()
        .unwrap();
    assert!(auth.capability.is_none());
    let audit = execution::get_execution_audit_bundle(&conn, "maker-main", "intent-85")
        .unwrap()
        .unwrap();
    assert!(audit.ingress.is_empty());
    let report =
        execution::verify_execution_audit_integrity(&conn, "maker-main", "intent-85").unwrap();
    assert!(!report.valid);

    assert_eq!(conn.total_changes(), before);
    let participant: Option<String> = conn
        .query_row(
            "SELECT participant_id FROM ingress_provenance WHERE delivery_id = 'delivery-85'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert!(participant.is_none());
    let capability: Option<String> = conn
        .query_row(
            "SELECT capability FROM execution_authorization_provenance
             WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert!(capability.is_none());
}

#[test]
fn explicit_execution_migration_preserves_conservative_backfill_rules() {
    let conn = fixture();
    seed_valid(&conn);
    conn.execute(
        "UPDATE ingress_provenance SET participant_id = NULL WHERE delivery_id = 'delivery-85'",
        [],
    )
    .unwrap();
    conn.execute(
        "UPDATE execution_authorization_provenance
         SET principal_provider = NULL, principal_subject = NULL,
             capability = NULL, resource = NULL
         WHERE participant_id = 'maker-main' AND intent_id = 'intent-85'",
        [],
    )
    .unwrap();

    execution::migrate_execution_schema(&conn).unwrap();

    let participant: Option<String> = conn
        .query_row(
            "SELECT participant_id FROM ingress_provenance WHERE delivery_id = 'delivery-85'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(participant.as_deref(), Some("maker-main"));
    let provenance = execution::get_authorization_provenance(&conn, "maker-main", "intent-85")
        .unwrap()
        .unwrap();
    assert_eq!(provenance.capability.as_deref(), Some("post_message"));
    assert!(provenance.principal.is_none());
    assert!(provenance.resource.is_none());
}

'''
s = replace_once(s, marker, tests + marker, "new read-only tests")
p.write_text(s, encoding="utf-8")

# The renamed migration function may be referenced in other Rust tests. Keep the
# refactor explicit anywhere the old helper name still appears under src/.
for path in Path("src").glob("*.rs"):
    s = path.read_text(encoding="utf-8")
    if "ensure_execution_tables" in s:
        s = s.replace("ensure_execution_tables", "migrate_execution_schema")
        path.write_text(s, encoding="utf-8")
