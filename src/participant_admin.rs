use std::{error::Error, path::PathBuf};

use clap::Subcommand;
use rusqlite::{Connection, OptionalExtension};

use crate::{db, github_webhook, identity, participant_auth, web_auth};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Clone, Eq, PartialEq)]
struct ParticipantInspection {
    participant_id: String,
    source: String,
    label: Option<String>,
    role: String,
    lifecycle_status: String,
    totp_status: &'static str,
    auth_scheme: Option<String>,
    auth_status: &'static str,
    owner_provider: Option<String>,
    owner_subject: Option<String>,
    owner_login: Option<String>,
}

#[derive(Debug, Subcommand)]
pub enum ParticipantCommand {
    /// Provision a participant identity shared by human TOTP and agent authentication.
    Provision {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        source: String,
        #[arg(long)]
        label: Option<String>,
    },
    /// Show one participant's non-secret identity and credential status.
    Show {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
    /// List participants with compact non-secret credential status.
    List {
        #[arg(long)]
        db: PathBuf,
    },
    /// Deactivate a participant without deleting identity history or credentials.
    Deactivate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
    /// Reactivate a previously inactive participant.
    Reactivate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
    /// Set the participant role used by Human-Web authorization.
    SetRole {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long, value_parser = ["user", "admin"])]
        role: String,
    },
    /// Bind a participant to an external owner identity for delegated transport attribution.
    SetOwner {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long, value_parser = ["github"])]
        provider: String,
        #[arg(long)]
        subject: String,
        #[arg(long)]
        login: Option<String>,
    },
    /// Remove external owner metadata without deleting the participant.
    ClearOwner {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
    /// Generate and register a TOTP secret for human browser login.
    TotpEnroll {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long, default_value = "ConversationBlackboard")]
        issuer: String,
    },
    /// Revoke human TOTP login for a participant.
    TotpRevoke {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
    /// Generate the participant HMAC secret. Fails if auth is already configured.
    AuthGenerate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
    /// Rotate the participant HMAC secret and print the replacement exactly once.
    AuthRotate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
    /// Revoke participant HMAC authentication without deleting the participant.
    AuthRevoke {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
}

pub fn dispatch(command: ParticipantCommand) -> DynResult {
    match command {
        ParticipantCommand::Provision {
            db: path,
            participant_id,
            source,
            label,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            github_webhook::ensure_owner_columns(&conn)?;
            let record = identity::provision_web_participant_identity(
                &conn,
                &participant_id,
                &source,
                label.as_deref(),
            )?
            .ok_or_else(|| format!("participant already exists: {participant_id}"))?;
            println!("PARTICIPANT READY");
            println!("source         : {}", record.source);
            println!("participant_id : {}", record.instance);
            println!("role           : user");
            println!("status         : active");
            Ok(())
        }
        ParticipantCommand::Show {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            github_webhook::ensure_owner_columns(&conn)?;
            let record = inspect_participant(&conn, &participant_id)?
                .ok_or_else(|| format!("unknown participant: {participant_id}"))?;
            print_participant(&record);
            Ok(())
        }
        ParticipantCommand::List { db: path } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            github_webhook::ensure_owner_columns(&conn)?;
            let records = list_participants(&conn)?;
            println!("PARTICIPANTS");
            println!("participant_id\tsource\trole\tstatus\ttotp\tauth\towner\tlabel");
            for record in records {
                let owner = match (
                    record.owner_provider.as_deref(),
                    record.owner_subject.as_deref(),
                    record.owner_login.as_deref(),
                ) {
                    (Some(provider), Some(subject), Some(login)) => {
                        format!("{provider}:{subject}({login})")
                    }
                    (Some(provider), Some(subject), None) => format!("{provider}:{subject}"),
                    _ => "-".to_owned(),
                };
                println!(
                    "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                    record.participant_id,
                    record.source,
                    record.role,
                    record.lifecycle_status,
                    record.totp_status,
                    record.auth_status,
                    owner,
                    record.label.as_deref().unwrap_or("-")
                );
            }
            Ok(())
        }
        ParticipantCommand::Deactivate {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !identity::set_web_participant_status(&conn, &participant_id, "inactive")? {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            println!("PARTICIPANT INACTIVE");
            println!("participant_id : {participant_id}");
            println!("status         : inactive");
            Ok(())
        }
        ParticipantCommand::Reactivate {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !identity::set_web_participant_status(&conn, &participant_id, "active")? {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            println!("PARTICIPANT ACTIVE");
            println!("participant_id : {participant_id}");
            println!("status         : active");
            Ok(())
        }
        ParticipantCommand::SetRole {
            db: path,
            participant_id,
            role,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !identity::set_web_participant_role(&conn, &participant_id, &role)? {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            println!("PARTICIPANT ROLE READY");
            println!("participant_id : {participant_id}");
            println!("role           : {role}");
            Ok(())
        }
        ParticipantCommand::SetOwner {
            db: path,
            participant_id,
            provider,
            subject,
            login,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !github_webhook::set_participant_owner(
                &conn,
                &participant_id,
                &provider,
                &subject,
                login.as_deref(),
            )? {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            println!("PARTICIPANT OWNER READY");
            println!("participant_id : {participant_id}");
            println!("provider       : {provider}");
            println!("subject        : {subject}");
            println!("login          : {}", login.as_deref().unwrap_or("-"));
            Ok(())
        }
        ParticipantCommand::ClearOwner {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !github_webhook::clear_participant_owner(&conn, &participant_id)? {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            println!("PARTICIPANT OWNER CLEARED");
            println!("participant_id : {participant_id}");
            Ok(())
        }
        ParticipantCommand::TotpEnroll {
            db: path,
            participant_id,
            issuer,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if identity::get_web_participant(&conn, &participant_id)?.is_none() {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            let secret = web_auth::generate_totp_secret();
            if !identity::set_web_participant_totp(&conn, &participant_id, &secret)? {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            let uri = web_auth::otpauth_uri(&participant_id, &secret, &issuer)
                .ok_or("could not construct TOTP enrollment URI")?;
            println!("TOTP ENROLLMENT READY");
            println!("participant_id : {participant_id}");
            println!("issuer         : {issuer}");
            println!("setup_key      : {secret}");
            println!("otpauth_uri    : {uri}");
            Ok(())
        }
        ParticipantCommand::TotpRevoke {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !identity::revoke_web_participant_totp(&conn, &participant_id)? {
                return Err(format!("no active TOTP for participant: {participant_id}").into());
            }
            println!("revoked TOTP login: {participant_id}");
            Ok(())
        }
        ParticipantCommand::AuthGenerate {
            db: path,
            participant_id,
        } => provision_auth(&path, &participant_id, false),
        ParticipantCommand::AuthRotate {
            db: path,
            participant_id,
        } => provision_auth(&path, &participant_id, true),
        ParticipantCommand::AuthRevoke {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !identity::revoke_web_participant_auth(&conn, &participant_id)? {
                return Err(format!("no active participant auth for: {participant_id}").into());
            }
            println!("PARTICIPANT AUTH REVOKED");
            println!("participant_id : {participant_id}");
            Ok(())
        }
    }
}

fn provision_auth(path: &std::path::Path, participant_id: &str, rotate: bool) -> DynResult {
    require_database(path)?;
    let conn = db::connect(path)?;
    if identity::get_web_participant(&conn, participant_id)?.is_none() {
        return Err(format!("unknown participant: {participant_id}").into());
    }
    let current = inspect_participant(&conn, participant_id)?
        .ok_or_else(|| format!("unknown participant: {participant_id}"))?;
    if !rotate && current.auth_status == "active" {
        return Err(format!(
            "participant auth already configured: {participant_id}; use auth-rotate"
        )
        .into());
    }

    let (secret, registered_secret) = participant_auth::generate_secret();
    debug_assert_eq!(secret, registered_secret);
    if !identity::set_web_participant_auth_secret(&conn, participant_id, &registered_secret)? {
        return Err(format!("unknown participant: {participant_id}").into());
    }

    println!("PARTICIPANT AUTH READY");
    println!("participant_id : {participant_id}");
    println!("auth_scheme    : {}", participant_auth::AUTH_SCHEME);
    println!("secret         : {secret}");
    println!("note           : This secret is shown once. Store it with the participant client; participant show/list never reveal it.");
    Ok(())
}

fn inspect_participant(
    conn: &Connection,
    participant_id: &str,
) -> rusqlite::Result<Option<ParticipantInspection>> {
    github_webhook::ensure_owner_columns(conn)?;
    conn.query_row(
        "SELECT participant_id, source, label, role, status, totp_secret, auth_scheme, auth_secret,\n                owner_provider, owner_subject, owner_login\n         FROM web_participants\n         WHERE participant_id = ?1\n         LIMIT 1",
        [participant_id],
        participant_row,
    )
    .optional()
}

fn list_participants(conn: &Connection) -> rusqlite::Result<Vec<ParticipantInspection>> {
    github_webhook::ensure_owner_columns(conn)?;
    let mut stmt = conn.prepare(
        "SELECT participant_id, source, label, role, status, totp_secret, auth_scheme, auth_secret,\n                owner_provider, owner_subject, owner_login\n         FROM web_participants\n         ORDER BY participant_id ASC",
    )?;
    let rows = stmt.query_map([], participant_row)?;
    rows.collect()
}

fn participant_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ParticipantInspection> {
    let totp_secret: Option<String> = row.get(5)?;
    let auth_scheme: Option<String> = row.get(6)?;
    let auth_secret: Option<String> = row.get(7)?;
    Ok(ParticipantInspection {
        participant_id: row.get(0)?,
        source: row.get(1)?,
        label: row.get(2)?,
        role: row.get(3)?,
        lifecycle_status: row.get(4)?,
        totp_status: if totp_secret.is_some() {
            "active"
        } else {
            "not-configured"
        },
        auth_status: if auth_scheme.as_deref() == Some(participant_auth::AUTH_SCHEME)
            && auth_secret
                .as_deref()
                .is_some_and(participant_auth::validate_secret)
        {
            "active"
        } else {
            "not-configured"
        },
        auth_scheme,
        owner_provider: row.get(8)?,
        owner_subject: row.get(9)?,
        owner_login: row.get(10)?,
    })
}

fn print_participant(record: &ParticipantInspection) {
    println!("PARTICIPANT");
    println!("participant_id : {}", record.participant_id);
    println!(
        "label          : {}",
        record.label.as_deref().unwrap_or("-")
    );
    println!("source         : {}", record.source);
    println!("role           : {}", record.role);
    println!("status         : {}", record.lifecycle_status);
    println!();
    println!("owner");
    println!(
        "provider       : {}",
        record.owner_provider.as_deref().unwrap_or("-")
    );
    println!(
        "subject        : {}",
        record.owner_subject.as_deref().unwrap_or("-")
    );
    println!(
        "login          : {}",
        record.owner_login.as_deref().unwrap_or("-")
    );
    println!();
    println!("totp");
    println!("status         : {}", record.totp_status);
    println!();
    println!("auth");
    println!("status         : {}", record.auth_status);
    println!(
        "auth_scheme    : {}",
        record.auth_scheme.as_deref().unwrap_or("-")
    );
}

fn require_database(path: &std::path::Path) -> DynResult {
    if path.is_file() {
        Ok(())
    } else {
        Err(format!("database does not exist: {}", path.display()).into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn inspection_never_exposes_hmac_secret() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        github_webhook::ensure_owner_columns(&conn).unwrap();
        identity::provision_web_participant_identity(&conn, "maker-main", "maker", None)
            .unwrap()
            .unwrap();
        let (_, secret) = participant_auth::generate_secret();
        identity::set_web_participant_auth_secret(&conn, "maker-main", &secret).unwrap();

        let record = inspect_participant(&conn, "maker-main").unwrap().unwrap();
        assert_eq!(record.auth_status, "active");
        assert_eq!(
            record.auth_scheme.as_deref(),
            Some(participant_auth::AUTH_SCHEME)
        );
        let debug = format!("{record:?}");
        assert!(!debug.contains(&secret));
    }

    #[test]
    fn lifecycle_state_remains_visible() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        github_webhook::ensure_owner_columns(&conn).unwrap();
        identity::provision_web_participant_identity(&conn, "maker-main", "maker", None)
            .unwrap()
            .unwrap();
        assert!(identity::set_web_participant_status(&conn, "maker-main", "inactive").unwrap());
        let record = inspect_participant(&conn, "maker-main").unwrap().unwrap();
        assert_eq!(record.lifecycle_status, "inactive");
    }

    #[test]
    fn github_owner_metadata_is_visible_but_not_secret() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        github_webhook::ensure_owner_columns(&conn).unwrap();
        identity::provision_web_participant_identity(&conn, "maker-main", "maker", None)
            .unwrap()
            .unwrap();
        github_webhook::set_participant_owner(
            &conn,
            "maker-main",
            "github",
            "543608",
            Some("cctsao1008"),
        )
        .unwrap();
        let record = inspect_participant(&conn, "maker-main").unwrap().unwrap();
        assert_eq!(record.owner_provider.as_deref(), Some("github"));
        assert_eq!(record.owner_subject.as_deref(), Some("543608"));
        assert_eq!(record.owner_login.as_deref(), Some("cctsao1008"));
    }
}
