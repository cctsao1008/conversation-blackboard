use std::{
    error::Error,
    path::{Path, PathBuf},
};

use clap::Subcommand;
use rusqlite::{params, Connection, OptionalExtension};

use crate::{authorization, db, identity};

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

#[derive(Debug, Clone)]
struct GrantCreateSpec<'a> {
    principal_provider: &'a str,
    principal_subject: &'a str,
    participant_id: &'a str,
    capability: &'a str,
    resource: Option<&'a str>,
    intent_id: Option<&'a str>,
    expires_at: Option<i64>,
    one_shot: bool,
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
    /// Deactivate a delegated grant without deleting history.
    Deactivate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        grant_id: i64,
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
            let conn = db::connect(&path)?;
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
    }
}

fn create_grant(conn: &Connection, spec: &GrantCreateSpec<'_>) -> DynResult<i64> {
    authorization::ensure_grant_schema(conn)?;

    let principal_provider = normalize(
        spec.principal_provider,
        MAX_PROVIDER_BYTES,
        "principal_provider",
    )?;
    let principal_subject = normalize(
        spec.principal_subject,
        MAX_SUBJECT_BYTES,
        "principal_subject",
    )?;
    let participant_id =
        identity::validate_participant_id(spec.participant_id).ok_or("invalid participant_id")?;
    let capability = normalize(spec.capability, MAX_CAPABILITY_BYTES, "capability")?;
    let resource = normalize_optional(spec.resource, MAX_RESOURCE_BYTES, "resource")?;
    let intent_id = match spec.intent_id {
        Some(value) => {
            Some(crate::execution::normalize_intent_id(value).map_err(|_| "invalid intent_id")?)
        }
        None => None,
    };

    let participant_status: Option<String> = conn
        .query_row(
            "SELECT status FROM web_participants WHERE participant_id = ?1",
            [&participant_id],
            |row| row.get(0),
        )
        .optional()?;
    match participant_status.as_deref() {
        Some("active") => {}
        Some(_) => return Err(format!("participant is not active: {participant_id}").into()),
        None => return Err(format!("unknown participant: {participant_id}").into()),
    }

    if let Some(expires_at) = spec.expires_at {
        let now: i64 = conn.query_row("SELECT unixepoch()", [], |row| row.get(0))?;
        if expires_at <= now {
            return Err("expires_at must be a future Unix timestamp".into());
        }
    }

    conn.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource, intent_id, expires_at, one_shot)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            principal_provider,
            principal_subject,
            participant_id,
            capability,
            resource,
            intent_id,
            spec.expires_at,
            i64::from(spec.one_shot)
        ],
    )?;
    Ok(conn.last_insert_rowid())
}

fn list_grants(
    conn: &Connection,
    participant_id: Option<&str>,
) -> rusqlite::Result<Vec<GrantInspection>> {
    authorization::ensure_grant_schema(conn)?;
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

fn deactivate_grant(conn: &Connection, grant_id: i64) -> rusqlite::Result<bool> {
    authorization::ensure_grant_schema(conn)?;
    let exists = conn
        .query_row(
            "SELECT 1 FROM delegated_grants WHERE id = ?1",
            [grant_id],
            |_| Ok(()),
        )
        .optional()?
        .is_some();
    if !exists {
        return Ok(false);
    }
    conn.execute(
        "UPDATE delegated_grants
         SET status = 'inactive', updated_at = unixepoch()
         WHERE id = ?1 AND status != 'inactive'",
        [grant_id],
    )?;
    Ok(true)
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
}
