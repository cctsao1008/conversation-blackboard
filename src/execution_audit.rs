use std::{
    error::Error,
    path::{Path, PathBuf},
};

use clap::Subcommand;

use crate::{db, execution, identity};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Subcommand)]
pub enum ExecutionCommand {
    /// Inspect immutable committed execution evidence without re-evaluating policy.
    Audit {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        intent_id: String,
    },
    /// Verify structural integrity of committed execution evidence without repairing it.
    VerifyAudit {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        intent_id: String,
    },
    /// Verify the entire committed execution corpus and discover orphan evidence.
    #[command(name = "verify-all")]
    VerifyAll {
        #[arg(long)]
        db: PathBuf,
    },
}

pub fn dispatch(command: ExecutionCommand) -> DynResult {
    match command {
        ExecutionCommand::Audit {
            db: path,
            participant_id,
            intent_id,
        } => {
            require_database(&path)?;
            let participant_id = identity::validate_participant_id(&participant_id)
                .ok_or("invalid participant_id")?;
            let intent_id =
                execution::normalize_intent_id(&intent_id).map_err(|_| "invalid intent_id")?;
            let conn = db::connect_read_only(&path)?;
            require_current_execution_schema(&conn, &path)?;
            let Some(audit) =
                execution::get_execution_audit_bundle(&conn, &participant_id, &intent_id)?
            else {
                return Err(format!("execution not found: {participant_id}/{intent_id}").into());
            };

            println!("EXECUTION AUDIT");
            println!("participant_id : {}", audit.receipt.participant_id);
            println!("intent_id      : {}", audit.receipt.intent_id);
            println!("capability     : {}", audit.receipt.capability);
            println!("message_id     : {}", audit.receipt.message_id);
            println!("status         : {}", audit.receipt.status);
            println!("intent_hash    : {}", audit.receipt.intent_hash);
            if let Some(auth) = audit.authorization {
                println!("auth_source    : {}", auth.source);
                println!("auth_reason    : {}", auth.reason);
                println!(
                    "auth_grant_id  : {}",
                    auth.grant_id
                        .map(|v| v.to_string())
                        .unwrap_or_else(|| "-".to_owned())
                );
            } else {
                println!("auth_source    : -");
                println!("auth_reason    : -");
                println!("auth_grant_id  : -");
            }
            println!("deliveries     : {}", audit.ingress.len());
            for delivery in audit.ingress {
                println!(
                    "delivery        : {}\t{}\t{}\t{}\t{}:{}\t{}",
                    delivery.participant_id,
                    delivery.delivery_id,
                    delivery.transport,
                    delivery.external_ref,
                    delivery.principal.provider,
                    delivery.principal.subject,
                    delivery.intent_id,
                );
            }
            Ok(())
        }
        ExecutionCommand::VerifyAudit {
            db: path,
            participant_id,
            intent_id,
        } => {
            require_database(&path)?;
            let participant_id = identity::validate_participant_id(&participant_id)
                .ok_or("invalid participant_id")?;
            let intent_id =
                execution::normalize_intent_id(&intent_id).map_err(|_| "invalid intent_id")?;
            let conn = db::connect_read_only(&path)?;
            require_current_execution_schema(&conn, &path)?;
            let report =
                execution::verify_execution_audit_integrity(&conn, &participant_id, &intent_id)?;
            println!("EXECUTION AUDIT INTEGRITY");
            println!("participant_id : {}", report.participant_id);
            println!("intent_id      : {}", report.intent_id);
            println!("valid          : {}", report.valid);
            for check in report.checks {
                println!("check          : {check}");
            }
            for violation in report.violations {
                println!("violation      : {violation}");
            }
            if report.valid {
                Ok(())
            } else {
                Err("execution audit integrity verification failed".into())
            }
        }
        ExecutionCommand::VerifyAll { db: path } => {
            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            require_current_execution_schema(&conn, &path)?;
            let report = execution::sweep_execution_audit_integrity(&conn)?;

            println!("EXECUTION AUDIT SWEEP");
            println!("valid              : {}", report.valid);
            println!("executions_scanned : {}", report.executions_scanned);
            println!("invalid_executions : {}", report.invalid_executions.len());
            println!("orphan_evidence    : {}", report.orphan_evidence.len());
            for invalid in &report.invalid_executions {
                println!(
                    "invalid_execution  : {}\t{}\t{}",
                    invalid.participant_id,
                    invalid.intent_id,
                    invalid.violations.join(",")
                );
            }
            for orphan in &report.orphan_evidence {
                println!(
                    "orphan_evidence     : {}\t{}\t{}\t{}",
                    orphan.kind,
                    orphan.participant_id.as_deref().unwrap_or("-"),
                    orphan.intent_id,
                    orphan.reference.as_deref().unwrap_or("-")
                );
            }

            if report.valid {
                Ok(())
            } else {
                Err("execution audit sweep verification failed".into())
            }
        }
    }
}

fn require_current_execution_schema(conn: &rusqlite::Connection, path: &Path) -> DynResult {
    if execution::execution_schema_current(conn)? {
        return Ok(());
    }
    Err(format!(
        "execution schema requires migration: run `conversation-blackboard db init --db {}` before audit",
        path.display()
    )
    .into())
}

fn require_database(path: &Path) -> DynResult {
    if !path.is_file() {
        return Err(format!("database does not exist: {}", path.display()).into());
    }
    Ok(())
}

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
