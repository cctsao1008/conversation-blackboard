use std::{
    error::Error,
    path::{Path, PathBuf},
};

use clap::Subcommand;
use rusqlite::Connection;

use crate::{
    authorization,
    authorization_admin::{
        self, AuthorizationAdministrationActor, DelegatedGrantCreateRequest as GrantCreateSpec,
        DurableGrantCreateOutcome, DurableGrantCreateRequest as DurableGrantCreateSpec,
        DurableGrantCreateState,
    },
    db,
    execution::Principal,
    identity,
};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

const MAX_PROVIDER_BYTES: usize = 256;
const MAX_SUBJECT_BYTES: usize = 256;
const MAX_CAPABILITY_BYTES: usize = 128;
const MAX_RESOURCE_BYTES: usize = 256;

#[derive(Debug, Clone, PartialEq, Eq)]
struct GrantInspection {
    id: i64,
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
    intent_id: Option<String>,
    expires_at: Option<i64>,
    one_shot: bool,
    consumed_at: Option<i64>,
    consumed_intent_id: Option<String>,
    status: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct DurableGrantInspection {
    id: i64,
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
    status: String,
    created_at: i64,
    updated_at: i64,
}

#[derive(Debug, Subcommand)]
pub enum DurableGrantCommand {
    /// Create or reactivate one durable scoped principal grant.
    Create {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        principal_provider: String,
        #[arg(long)]
        principal_subject: String,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        capability: String,
        #[arg(long)]
        resource: Option<String>,
    },
    /// List durable scoped principal grants and non-secret lifecycle state.
    List {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: Option<String>,
    },
    /// Deactivate one durable authority scope without deleting history.
    Deactivate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        grant_id: i64,
    },
}

#[derive(Debug, Subcommand)]
pub enum GrantCommand {
    /// Create a delegated authorization grant. No credential material is stored.
    Create {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        principal_provider: String,
        #[arg(long)]
        principal_subject: String,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        capability: String,
        #[arg(long)]
        resource: Option<String>,
        #[arg(long)]
        intent_id: Option<String>,
        /// Future Unix timestamp. Omit for a non-expiring grant.
        #[arg(long)]
        expires_at: Option<i64>,
        /// Consume authority with the first committed semantic execution.
        #[arg(long, default_value_t = false)]
        one_shot: bool,
    },
    /// List delegated grants and non-secret authorization state.
    List {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: Option<String>,
    },
    /// Read immutable local authorization-administration provenance.
    History {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: Option<String>,
        #[arg(long)]
        before: Option<i64>,
        #[arg(long)]
        after: Option<i64>,
        #[arg(long)]
        limit: Option<usize>,
        #[arg(long)]
        order: Option<String>,
    },
    /// Explain a read-only authorization decision using the authoritative policy evaluator.
    Explain {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        principal_provider: String,
        #[arg(long)]
        principal_subject: String,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        capability: String,
        #[arg(long)]
        resource: Option<String>,
        #[arg(long)]
        intent_id: Option<String>,
    },
    /// Deactivate a delegated grant without deleting history.
    Deactivate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        grant_id: i64,
    },
    /// Manage durable scoped principal grants separately from delegated grants.
    Durable {
        #[command(subcommand)]
        command: DurableGrantCommand,
    },
    /// Verify authorization-policy object integrity without repairing the database.
    Verify {
        #[arg(long)]
        db: PathBuf,
    },
}

pub fn dispatch(command: GrantCommand) -> DynResult {
    match command {
        GrantCommand::Create {
            db: path,
            principal_provider,
            principal_subject,
            participant_id,
            capability,
            resource,
            intent_id,
            expires_at,
            one_shot,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let spec = GrantCreateSpec {
                principal_provider: &principal_provider,
                principal_subject: &principal_subject,
                participant_id: &participant_id,
                capability: &capability,
                resource: resource.as_deref(),
                intent_id: intent_id.as_deref(),
                expires_at,
                one_shot,
            };
            let id = create_grant(&conn, &spec)?;
            println!("DELEGATED GRANT READY");
            println!("grant_id           : {id}");
            println!("principal_provider : {}", principal_provider.trim());
            println!("principal_subject  : {}", principal_subject.trim());
            println!("participant_id     : {}", participant_id.trim());
            println!("capability         : {}", capability.trim());
            println!(
                "resource           : {}",
                resource.as_deref().unwrap_or("*")
            );
            println!(
                "intent_id          : {}",
                intent_id.as_deref().unwrap_or("*")
            );
            println!(
                "expires_at         : {}",
                expires_at
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| "never".to_owned())
            );
            println!("one_shot           : {one_shot}");
            Ok(())
        }
        GrantCommand::List {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            if !authorization::authorization_policy_snapshot_schema_current(&conn)? {
                return Err(format!(
                    "authorization grant schema requires migration: run `conversation-blackboard db init --db {}` before list",
                    path.display()
                )
                .into());
            }
            let grants = list_grants(&conn, participant_id.as_deref())?;
            println!("DELEGATED GRANTS");
            println!("id\tprincipal\tparticipant_id\tcapability\tresource\tintent_id\texpires_at\tone_shot\tconsumed_at\tconsumed_intent_id\tstatus");
            for grant in grants {
                println!(
                    "{}\t{}:{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                    grant.id,
                    grant.principal_provider,
                    grant.principal_subject,
                    grant.participant_id,
                    grant.capability,
                    grant.resource.as_deref().unwrap_or("*"),
                    grant.intent_id.as_deref().unwrap_or("*"),
                    grant
                        .expires_at
                        .map(|v| v.to_string())
                        .unwrap_or_else(|| "never".to_owned()),
                    grant.one_shot,
                    grant
                        .consumed_at
                        .map(|v| v.to_string())
                        .unwrap_or_else(|| "-".to_owned()),
                    grant.consumed_intent_id.as_deref().unwrap_or("-"),
                    grant.status,
                );
            }
            Ok(())
        }
        GrantCommand::History {
            db: path,
            participant_id,
            before,
            after,
            limit,
            order,
        } => {
            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            if !authorization_admin::schema_current(&conn)? {
                return Err(format!(
                    "authorization administration schema requires migration: run `conversation-blackboard db init --db {}` before history",
                    path.display()
                )
                .into());
            }
            let window = authorization_admin::read_administration_event_window(
                &conn,
                authorization_admin::AuthorizationAdministrationHistoryWindowRequest {
                    participant_id: participant_id.as_deref(),
                    before,
                    after,
                    limit,
                    order: order.as_deref(),
                },
            )
            .map_err(|_| "invalid administration history window")?;
            println!("AUTHORIZATION ADMINISTRATION HISTORY");
            println!("order    : {}", window.order);
            println!("has_more : {}", window.has_more);
            println!("id\tstore\tgrant_id\toperation\tactor\tparticipant_id\tprincipal\tcapability\tresource\tintent_id\texpires_at\tone_shot\tbefore\tafter\tcreated_at");
            for event in window.events {
                let actor = match (
                    event.actor_provider.as_deref(),
                    event.actor_subject.as_deref(),
                ) {
                    (Some(provider), Some(subject)) => format!("{provider}:{subject}"),
                    _ => event.actor_surface.clone(),
                };
                println!(
                    "{}\t{}\t{}\t{}\t{}\t{}\t{}:{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                    event.id,
                    event.grant_store,
                    event.grant_id,
                    event.operation,
                    actor,
                    event.participant_id,
                    event.target_principal_provider,
                    event.target_principal_subject,
                    event.capability,
                    event.resource.as_deref().unwrap_or("*"),
                    event.intent_id.as_deref().unwrap_or("*"),
                    event
                        .expires_at
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "never".to_owned()),
                    event.one_shot,
                    event.before_status.as_deref().unwrap_or("-"),
                    event.after_status,
                    event.created_at,
                );
            }
            Ok(())
        }
        GrantCommand::Explain {
            db: path,
            principal_provider,
            principal_subject,
            participant_id,
            capability,
            resource,
            intent_id,
        } => {
            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            if !authorization::authorization_decision_schema_current(&conn)? {
                return Err(format!(
                    "authorization decision schema requires migration: run `conversation-blackboard db init --db {}` before explain",
                    path.display()
                )
                .into());
            }
            let principal = Principal {
                provider: normalize(
                    &principal_provider,
                    MAX_PROVIDER_BYTES,
                    "principal_provider",
                )?,
                subject: normalize(&principal_subject, MAX_SUBJECT_BYTES, "principal_subject")?,
            };
            let participant_id = identity::validate_participant_id(&participant_id)
                .ok_or("invalid participant_id")?;
            let capability = normalize(&capability, MAX_CAPABILITY_BYTES, "capability")?;
            let resource = normalize_optional(resource.as_deref(), MAX_RESOURCE_BYTES, "resource")?;
            let intent_id = match intent_id.as_deref() {
                Some(value) => Some(
                    crate::execution::normalize_intent_id(value)
                        .map_err(|_| "invalid intent_id")?,
                ),
                None => None,
            };
            let explanation = authorization::explain_authorization(
                &conn,
                &principal,
                &participant_id,
                &capability,
                resource.as_deref(),
                intent_id.as_deref(),
            )?;
            println!("AUTHORIZATION EXPLANATION");
            println!(
                "decision          : {}",
                if explanation.allowed { "allow" } else { "deny" }
            );
            println!("reason            : {}", explanation.reason);
            println!("source            : {}", explanation.source);
            println!(
                "grant_id          : {}",
                explanation
                    .grant_id
                    .map(|id| id.to_string())
                    .unwrap_or_else(|| "-".to_owned())
            );
            println!(
                "consume_on_commit : {}",
                explanation.consume_grant_id.is_some()
            );
            Ok(())
        }
        GrantCommand::Deactivate { db: path, grant_id } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if grant_id <= 0 {
                return Err("grant_id must be positive".into());
            }
            if !deactivate_grant(&conn, grant_id)? {
                return Err(format!("unknown delegated grant: {grant_id}").into());
            }
            println!("DELEGATED GRANT INACTIVE");
            println!("grant_id : {grant_id}");
            println!("status   : inactive");
            Ok(())
        }
        GrantCommand::Durable { command } => dispatch_durable(command),
        GrantCommand::Verify { db: path } => {
            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            if !authorization::authorization_integrity_schema_current(&conn)? {
                return Err(format!(
                    "authorization grant schema requires migration: run `conversation-blackboard db init --db {}` before audit",
                    path.display()
                )
                .into());
            }
            let report = authorization::audit_authorization_integrity(&conn)?;
            println!("AUTHORIZATION POLICY INTEGRITY");
            println!("valid                    : {}", report.valid);
            println!(
                "durable_grants_scanned   : {}",
                report.durable_grants_scanned
            );
            println!(
                "delegated_grants_scanned : {}",
                report.delegated_grants_scanned
            );
            println!("violations               : {}", report.violations.len());
            for violation in report.violations {
                println!(
                    "violation                : {}\t{}\t{}\t{}\t{}",
                    violation.kind,
                    violation.store,
                    violation
                        .grant_id
                        .map(|id| id.to_string())
                        .unwrap_or_else(|| "-".to_owned()),
                    violation.participant_id.as_deref().unwrap_or("-"),
                    violation.detail,
                );
            }
            if report.valid {
                Ok(())
            } else {
                Err("authorization policy integrity verification failed".into())
            }
        }
    }
}

fn dispatch_durable(command: DurableGrantCommand) -> DynResult {
    match command {
        DurableGrantCommand::Create {
            db: path,
            principal_provider,
            principal_subject,
            participant_id,
            capability,
            resource,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let spec = DurableGrantCreateSpec {
                principal_provider: &principal_provider,
                principal_subject: &principal_subject,
                participant_id: &participant_id,
                capability: &capability,
                resource: resource.as_deref(),
            };
            let outcome = create_durable_grant(&conn, &spec)?;
            let state = match outcome.state {
                DurableGrantCreateState::Created => "created",
                DurableGrantCreateState::Existing => "existing",
                DurableGrantCreateState::Reactivated => "reactivated",
            };
            println!("DURABLE PRINCIPAL GRANT READY");
            println!("grant_id           : {}", outcome.id);
            println!("state              : {state}");
            println!("principal_provider : {}", principal_provider.trim());
            println!("principal_subject  : {}", principal_subject.trim());
            println!("participant_id     : {}", participant_id.trim());
            println!("capability         : {}", capability.trim());
            println!(
                "resource           : {}",
                resource.as_deref().unwrap_or("*")
            );
            Ok(())
        }
        DurableGrantCommand::List {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect_read_only(&path)?;
            if !authorization::authorization_policy_snapshot_schema_current(&conn)? {
                return Err(format!(
                    "authorization grant schema requires migration: run `conversation-blackboard db init --db {}` before durable list",
                    path.display()
                )
                .into());
            }
            let grants = list_durable_grants(&conn, participant_id.as_deref())?;
            println!("DURABLE PRINCIPAL GRANTS");
            println!("id\tprincipal\tparticipant_id\tcapability\tresource\tstatus\tcreated_at\tupdated_at");
            for grant in grants {
                println!(
                    "{}\t{}:{}\t{}\t{}\t{}\t{}\t{}\t{}",
                    grant.id,
                    grant.principal_provider,
                    grant.principal_subject,
                    grant.participant_id,
                    grant.capability,
                    grant.resource.as_deref().unwrap_or("*"),
                    grant.status,
                    grant.created_at,
                    grant.updated_at,
                );
            }
            Ok(())
        }
        DurableGrantCommand::Deactivate { db: path, grant_id } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if grant_id <= 0 {
                return Err("grant_id must be positive".into());
            }
            let Some(updated_rows) = deactivate_durable_grant(&conn, grant_id)? else {
                return Err(format!("unknown durable principal grant: {grant_id}").into());
            };
            println!("DURABLE PRINCIPAL GRANT INACTIVE");
            println!("grant_id      : {grant_id}");
            println!("status        : inactive");
            println!("rows_changed  : {updated_rows}");
            Ok(())
        }
    }
}

fn create_durable_grant(
    conn: &Connection,
    spec: &DurableGrantCreateSpec<'_>,
) -> DynResult<DurableGrantCreateOutcome> {
    authorization_admin::create_durable_grant(
        conn,
        &AuthorizationAdministrationActor::local_cli(),
        spec,
    )
}

fn list_durable_grants(
    conn: &Connection,
    participant_id: Option<&str>,
) -> rusqlite::Result<Vec<DurableGrantInspection>> {
    let mut query = String::from(
        "SELECT id, principal_provider, principal_subject, participant_id, capability,
                resource, status, created_at, updated_at
         FROM principal_grants",
    );
    if participant_id.is_some() {
        query.push_str(" WHERE participant_id = ?1");
    }
    query.push_str(" ORDER BY id");

    let mut stmt = conn.prepare(&query)?;
    let map_row = |row: &rusqlite::Row<'_>| {
        Ok(DurableGrantInspection {
            id: row.get(0)?,
            principal_provider: row.get(1)?,
            principal_subject: row.get(2)?,
            participant_id: row.get(3)?,
            capability: row.get(4)?,
            resource: row.get(5)?,
            status: row.get(6)?,
            created_at: row.get(7)?,
            updated_at: row.get(8)?,
        })
    };
    if let Some(participant_id) = participant_id {
        stmt.query_map([participant_id], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>()
    } else {
        stmt.query_map([], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>()
    }
}

fn deactivate_durable_grant(conn: &Connection, grant_id: i64) -> DynResult<Option<usize>> {
    Ok(authorization_admin::deactivate_durable_grant(
        conn,
        &AuthorizationAdministrationActor::local_cli(),
        grant_id,
    )?
    .map(|outcome| outcome.rows_changed))
}

fn create_grant(conn: &Connection, spec: &GrantCreateSpec<'_>) -> DynResult<i64> {
    Ok(authorization_admin::create_delegated_grant(
        conn,
        &AuthorizationAdministrationActor::local_cli(),
        spec,
    )?
    .id)
}

fn list_grants(
    conn: &Connection,
    participant_id: Option<&str>,
) -> rusqlite::Result<Vec<GrantInspection>> {
    let mut query = String::from(
        "SELECT id, principal_provider, principal_subject, participant_id, capability,
                resource, intent_id, expires_at, one_shot, consumed_at, consumed_intent_id, status
         FROM delegated_grants",
    );
    if participant_id.is_some() {
        query.push_str(" WHERE participant_id = ?1");
    }
    query.push_str(" ORDER BY id");

    let mut stmt = conn.prepare(&query)?;
    let map_row = |row: &rusqlite::Row<'_>| {
        Ok(GrantInspection {
            id: row.get(0)?,
            principal_provider: row.get(1)?,
            principal_subject: row.get(2)?,
            participant_id: row.get(3)?,
            capability: row.get(4)?,
            resource: row.get(5)?,
            intent_id: row.get(6)?,
            expires_at: row.get(7)?,
            one_shot: row.get::<_, i64>(8)? != 0,
            consumed_at: row.get(9)?,
            consumed_intent_id: row.get(10)?,
            status: row.get(11)?,
        })
    };
    if let Some(participant_id) = participant_id {
        stmt.query_map([participant_id], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>()
    } else {
        stmt.query_map([], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>()
    }
}

fn deactivate_grant(conn: &Connection, grant_id: i64) -> DynResult<bool> {
    Ok(authorization_admin::deactivate_delegated_grant(
        conn,
        &AuthorizationAdministrationActor::local_cli(),
        grant_id,
    )?
    .is_some())
}

fn normalize(value: &str, max_bytes: usize, field: &'static str) -> DynResult<String> {
    let value = value.trim();
    if value.is_empty() || value.len() > max_bytes || value.chars().any(char::is_control) {
        return Err(format!("invalid {field}").into());
    }
    Ok(value.to_owned())
}

fn normalize_optional(
    value: Option<&str>,
    max_bytes: usize,
    field: &'static str,
) -> DynResult<Option<String>> {
    value
        .map(|value| normalize(value, max_bytes, field))
        .transpose()
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
    use tempfile::tempdir;

    fn setup() -> (tempfile::TempDir, Connection) {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        identity::provision_web_participant_identity(&conn, "maker-main", "maker", Some("Maker"))
            .unwrap()
            .unwrap();
        (dir, conn)
    }

    #[test]
    fn grant_explain_refuses_legacy_schema_without_mutation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("legacy-explain.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                owner_provider TEXT,
                owner_subject TEXT
            );
            INSERT INTO web_participants (participant_id, status, role)
            VALUES ('maker-main', 'active', 'admin');",
        )
        .unwrap();
        let before: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        drop(conn);

        let error = dispatch(GrantCommand::Explain {
            db: path.clone(),
            principal_provider: "oidc:https://issuer.example".to_owned(),
            principal_subject: "agent-1".to_owned(),
            participant_id: "maker-main".to_owned(),
            capability: authorization::POST_MESSAGE.to_owned(),
            resource: Some("control-systems".to_owned()),
            intent_id: None,
        })
        .expect_err("legacy decision schema must be refused")
        .to_string();
        assert!(error.contains("authorization decision schema requires migration"));

        let conn = Connection::open(&path).unwrap();
        let after: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before, after);
        let grant_tables: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'table' AND name IN ('principal_grants', 'delegated_grants')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(grant_tables, 0);
    }

    #[test]
    fn grant_history_accepts_current_schema() {
        let (dir, conn) = setup();
        let spec = DurableGrantCreateSpec {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "history-agent",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: None,
        };
        create_durable_grant(&conn, &spec).unwrap();
        drop(conn);
        dispatch(GrantCommand::History {
            db: dir.path().join("board.db"),
            participant_id: Some("maker-main".to_owned()),
            before: None,
            after: None,
            limit: None,
            order: None,
        })
        .unwrap();
    }

    #[test]
    fn list_and_history_refuse_legacy_schema_without_mutation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("legacy-read.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );",
        )
        .unwrap();
        let before: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        drop(conn);

        for error in [
            dispatch(GrantCommand::List {
                db: path.clone(),
                participant_id: None,
            })
            .expect_err("delegated list must refuse legacy schema")
            .to_string(),
            dispatch(GrantCommand::Durable {
                command: DurableGrantCommand::List {
                    db: path.clone(),
                    participant_id: None,
                },
            })
            .expect_err("durable list must refuse legacy schema")
            .to_string(),
            dispatch(GrantCommand::History {
                db: path.clone(),
                participant_id: None,
                before: None,
                after: None,
                limit: None,
                order: None,
            })
            .expect_err("history must refuse legacy schema")
            .to_string(),
        ] {
            assert!(error.contains("schema requires migration"));
        }

        let conn = Connection::open(&path).unwrap();
        let after: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before, after);
        let policy_tables: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'table'
                   AND name IN ('principal_grants', 'delegated_grants', 'authorization_admin_events')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(policy_tables, 0);
    }

    #[test]
    fn grant_verify_accepts_clean_database() {
        let (dir, conn) = setup();
        drop(conn);
        dispatch(GrantCommand::Verify {
            db: dir.path().join("board.db"),
        })
        .unwrap();
    }

    #[test]
    fn grant_verify_refuses_legacy_schema_without_mutation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("legacy.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );",
        )
        .unwrap();
        let before: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        drop(conn);

        let error = dispatch(GrantCommand::Verify { db: path.clone() })
            .expect_err("legacy grant schema must be refused")
            .to_string();
        assert!(error.contains("authorization grant schema requires migration"));

        let conn = Connection::open(&path).unwrap();
        let after: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before, after);
        let grant_tables: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'table' AND name IN ('principal_grants', 'delegated_grants')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(grant_tables, 0);
    }

    #[test]
    fn grant_verify_reports_invalid_state_without_repairing_it() {
        let (dir, conn) = setup();
        for _ in 0..2 {
            conn.execute(
                "INSERT INTO principal_grants
                    (principal_provider, principal_subject, participant_id, capability, resource)
                 VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', NULL)",
                [],
            )
            .unwrap();
        }
        let before: i64 = conn
            .query_row("SELECT COUNT(*) FROM principal_grants", [], |row| {
                row.get(0)
            })
            .unwrap();
        drop(conn);

        let error = dispatch(GrantCommand::Verify {
            db: dir.path().join("board.db"),
        })
        .expect_err("duplicate wildcard authority must fail verification")
        .to_string();
        assert!(error.contains("authorization policy integrity verification failed"));

        let conn = db::connect(&dir.path().join("board.db")).unwrap();
        let after: i64 = conn
            .query_row("SELECT COUNT(*) FROM principal_grants", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(before, after);
    }

    #[test]
    fn durable_create_is_idempotent_and_reactivates_same_scope() {
        let (_dir, conn) = setup();
        let spec = DurableGrantCreateSpec {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: Some("control-systems"),
        };
        let created = create_durable_grant(&conn, &spec).unwrap();
        assert_eq!(created.state, DurableGrantCreateState::Created);
        let existing = create_durable_grant(&conn, &spec).unwrap();
        assert_eq!(existing.id, created.id);
        assert_eq!(existing.state, DurableGrantCreateState::Existing);
        assert_eq!(
            list_durable_grants(&conn, Some("maker-main"))
                .unwrap()
                .len(),
            1
        );

        assert_eq!(
            deactivate_durable_grant(&conn, created.id).unwrap(),
            Some(1)
        );
        assert_eq!(
            deactivate_durable_grant(&conn, created.id).unwrap(),
            Some(0)
        );
        let reactivated = create_durable_grant(&conn, &spec).unwrap();
        assert_eq!(reactivated.id, created.id);
        assert_eq!(reactivated.state, DurableGrantCreateState::Reactivated);
        let grant = list_durable_grants(&conn, Some("maker-main"))
            .unwrap()
            .remove(0);
        assert_eq!(grant.status, "active");
    }

    #[test]
    fn durable_create_rejects_unknown_inactive_and_unsupported_capability() {
        let (_dir, conn) = setup();
        let unknown = DurableGrantCreateSpec {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "missing-main",
            capability: authorization::POST_MESSAGE,
            resource: None,
        };
        assert!(create_durable_grant(&conn, &unknown).is_err());

        identity::set_web_participant_status(&conn, "maker-main", "inactive").unwrap();
        let inactive = DurableGrantCreateSpec {
            participant_id: "maker-main",
            ..unknown.clone()
        };
        assert!(create_durable_grant(&conn, &inactive).is_err());
        identity::set_web_participant_status(&conn, "maker-main", "active").unwrap();

        let unsupported = DurableGrantCreateSpec {
            capability: "invented_capability",
            ..inactive
        };
        assert!(create_durable_grant(&conn, &unsupported).is_err());
    }

    #[test]
    fn durable_resource_scope_remains_authoritative_over_implicit_hmac_fallback() {
        let (_dir, conn) = setup();
        let spec = DurableGrantCreateSpec {
            principal_provider: "participant-hmac",
            principal_subject: "maker-main",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: Some("blackboard-lounge"),
        };
        create_durable_grant(&conn, &spec).unwrap();
        let principal = Principal {
            provider: "participant-hmac".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let decision = authorization::evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            authorization::POST_MESSAGE,
            Some("control-systems"),
            None,
        )
        .unwrap();
        assert!(!decision.allowed);
        assert_eq!(decision.reason, "explicit_resource_scope_mismatch");
    }

    #[test]
    fn durable_deactivate_closes_legacy_duplicate_wildcard_scope() {
        let (_dir, conn) = setup();
        authorization::ensure_grant_schema(&conn).unwrap();
        for _ in 0..2 {
            conn.execute(
                "INSERT INTO principal_grants
                    (principal_provider, principal_subject, participant_id, capability, resource)
                 VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', NULL)",
                [],
            )
            .unwrap();
        }
        let grants = list_durable_grants(&conn, Some("maker-main")).unwrap();
        assert_eq!(grants.len(), 2);
        assert_eq!(
            deactivate_durable_grant(&conn, grants[0].id).unwrap(),
            Some(2)
        );
        let grants = list_durable_grants(&conn, Some("maker-main")).unwrap();
        assert!(grants.iter().all(|grant| grant.status == "inactive"));
    }

    #[test]
    fn create_list_and_deactivate_round_trip() {
        let (_dir, conn) = setup();
        let now: i64 = conn
            .query_row("SELECT unixepoch()", [], |row| row.get(0))
            .unwrap();
        let spec = GrantCreateSpec {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "maker-main",
            capability: "post_message",
            resource: Some("control-systems"),
            intent_id: Some("intent-79"),
            expires_at: Some(now + 3600),
            one_shot: true,
        };
        let id = create_grant(&conn, &spec).unwrap();
        let grants = list_grants(&conn, Some("maker-main")).unwrap();
        assert_eq!(grants.len(), 1);
        let grant = &grants[0];
        assert_eq!(grant.id, id);
        assert_eq!(grant.resource.as_deref(), Some("control-systems"));
        assert_eq!(grant.intent_id.as_deref(), Some("intent-79"));
        assert_eq!(grant.expires_at, Some(now + 3600));
        assert!(grant.one_shot);
        assert_eq!(grant.status, "active");

        assert!(deactivate_grant(&conn, id).unwrap());
        assert!(deactivate_grant(&conn, id).unwrap());
        let grant = list_grants(&conn, Some("maker-main")).unwrap().remove(0);
        assert_eq!(grant.status, "inactive");
    }

    #[test]
    fn create_rejects_unknown_inactive_and_expired_scope() {
        let (_dir, conn) = setup();
        let unknown = GrantCreateSpec {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "missing-main",
            capability: "post_message",
            resource: None,
            intent_id: None,
            expires_at: None,
            one_shot: false,
        };
        assert!(create_grant(&conn, &unknown).is_err());

        identity::set_web_participant_status(&conn, "maker-main", "inactive").unwrap();
        let inactive = GrantCreateSpec {
            participant_id: "maker-main",
            ..unknown.clone()
        };
        assert!(create_grant(&conn, &inactive).is_err());
        identity::set_web_participant_status(&conn, "maker-main", "active").unwrap();

        let now: i64 = conn
            .query_row("SELECT unixepoch()", [], |row| row.get(0))
            .unwrap();
        let expired = GrantCreateSpec {
            expires_at: Some(now),
            ..inactive
        };
        assert!(create_grant(&conn, &expired).is_err());
    }

    #[test]
    fn create_rejects_blank_or_control_values() {
        let (_dir, conn) = setup();
        let blank = GrantCreateSpec {
            principal_provider: " ",
            principal_subject: "agent-1",
            participant_id: "maker-main",
            capability: "post_message",
            resource: None,
            intent_id: None,
            expires_at: None,
            one_shot: false,
        };
        assert!(create_grant(&conn, &blank).is_err());

        let control = GrantCreateSpec {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent\n1",
            ..blank
        };
        assert!(create_grant(&conn, &control).is_err());
    }

    #[test]
    fn explain_reports_explicit_resource_scope_mismatch() {
        let (_dir, conn) = setup();
        authorization::ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', 'blackboard-lounge')",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "agent-1".to_owned(),
        };
        let explanation = authorization::explain_authorization(
            &conn,
            &principal,
            "maker-main",
            authorization::POST_MESSAGE,
            Some("control-systems"),
            None,
        )
        .unwrap();
        assert!(!explanation.allowed);
        assert_eq!(explanation.reason, "explicit_resource_scope_mismatch");
    }

    #[test]
    fn explain_reports_expired_delegated_grant() {
        let (_dir, conn) = setup();
        authorization::ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource, intent_id, expires_at, one_shot)
             VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', 'control-systems', 'intent-80', unixepoch() - 1, 1)",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "agent-1".to_owned(),
        };
        let explanation = authorization::explain_authorization(
            &conn,
            &principal,
            "maker-main",
            authorization::POST_MESSAGE,
            Some("control-systems"),
            Some("intent-80"),
        )
        .unwrap();
        assert!(!explanation.allowed);
        assert_eq!(explanation.reason, "delegated_grant_expired");
        assert_eq!(explanation.source, "delegated_grants");
    }
}
