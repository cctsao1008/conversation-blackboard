use std::{error::Error, path::PathBuf};

use clap::Subcommand;
use rusqlite::{Connection, OptionalExtension};

use crate::{db, identity, signed_auth, web_auth};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Clone, Eq, PartialEq)]
struct ParticipantInspection {
    participant_id: String,
    source: String,
    label: Option<String>,
    role: String,
    lifecycle_status: String,
    totp_status: &'static str,
    signature_scheme: Option<String>,
    public_key: Option<String>,
    signing_status: &'static str,
}

#[derive(Debug, Subcommand)]
pub enum ParticipantCommand {
    /// Provision a participant identity shared by human TOTP and agent signing.
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
    /// Generate an Ed25519 signing keypair without registering the private key.
    GenerateSigningKey,
    /// Register or rotate the Ed25519 public signing key used by agents.
    SetSigningKey {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        public_key: String,
    },
    /// Revoke the Ed25519 public signing key used by agents.
    RevokeSigningKey {
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
            let record = inspect_participant(&conn, &participant_id)?
                .ok_or_else(|| format!("unknown participant: {participant_id}"))?;
            print_participant(&record);
            Ok(())
        }
        ParticipantCommand::List { db: path } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let records = list_participants(&conn)?;
            println!("PARTICIPANTS");
            println!("participant_id\tsource\trole\tstatus\ttotp\tsigning\tlabel");
            for record in records {
                println!(
                    "{}\t{}\t{}\t{}\t{}\t{}\t{}",
                    record.participant_id,
                    record.source,
                    record.role,
                    record.lifecycle_status,
                    record.totp_status,
                    record.signing_status,
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
            let status = identity::get_web_participant_status(&conn, &participant_id)?
                .ok_or_else(|| format!("unknown participant: {participant_id}"))?;
            println!("PARTICIPANT INACTIVE");
            println!("participant_id : {participant_id}");
            println!("status         : {status}");
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
            let status = identity::get_web_participant_status(&conn, &participant_id)?
                .ok_or_else(|| format!("unknown participant: {participant_id}"))?;
            println!("PARTICIPANT ACTIVE");
            println!("participant_id : {participant_id}");
            println!("status         : {status}");
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
            println!(
                "note            : Add this account to any RFC 6238 authenticator, then use the 6-digit code on the browser UI."
            );
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
        ParticipantCommand::GenerateSigningKey => {
            let (private_key, public_key) = signed_auth::generate_keypair();
            println!("PARTICIPANT SIGNING KEY MATERIAL");
            println!("signature_scheme : {}", signed_auth::SIGNATURE_SCHEME);
            println!("public_key       : {public_key}");
            println!("private_key      : {private_key}");
            println!(
                "note             : Register only the public key. Keep the private key with the agent participant and never send it to Conversation Blackboard."
            );
            Ok(())
        }
        ParticipantCommand::SetSigningKey {
            db: path,
            participant_id,
            public_key,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if identity::get_web_participant(&conn, &participant_id)?.is_none() {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            if !signed_auth::validate_public_key(&public_key) {
                return Err("invalid Ed25519 public key material".into());
            }
            if !identity::set_web_participant_signing_key(&conn, &participant_id, &public_key)? {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            println!("PARTICIPANT SIGNING KEY READY");
            println!("participant_id   : {participant_id}");
            println!("signature_scheme : {}", signed_auth::SIGNATURE_SCHEME);
            println!("public_key       : {public_key}");
            Ok(())
        }
        ParticipantCommand::RevokeSigningKey {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !identity::revoke_web_participant_signing_key(&conn, &participant_id)? {
                return Err(
                    format!("no active signing key for participant: {participant_id}").into(),
                );
            }
            println!("revoked participant signing key: {participant_id}");
            Ok(())
        }
    }
}

fn inspect_participant(
    conn: &Connection,
    participant_id: &str,
) -> rusqlite::Result<Option<ParticipantInspection>> {
    conn.query_row(
        "SELECT participant_id, source, label, role, status, totp_secret, signature_scheme, public_key\n         FROM web_participants\n         WHERE participant_id = ?1\n         LIMIT 1",
        [participant_id],
        |row| {
            let totp_secret: Option<String> = row.get(5)?;
            let signature_scheme: Option<String> = row.get(6)?;
            let public_key: Option<String> = row.get(7)?;
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
                signing_status: if signature_scheme.is_some() && public_key.is_some() {
                    "active"
                } else {
                    "not-configured"
                },
                signature_scheme,
                public_key,
            })
        },
    )
    .optional()
}

fn list_participants(conn: &Connection) -> rusqlite::Result<Vec<ParticipantInspection>> {
    let mut stmt = conn.prepare(
        "SELECT participant_id, source, label, role, status, totp_secret, signature_scheme, public_key\n         FROM web_participants\n         ORDER BY participant_id ASC",
    )?;
    let rows = stmt.query_map([], |row| {
        let totp_secret: Option<String> = row.get(5)?;
        let signature_scheme: Option<String> = row.get(6)?;
        let public_key: Option<String> = row.get(7)?;
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
            signing_status: if signature_scheme.is_some() && public_key.is_some() {
                "active"
            } else {
                "not-configured"
            },
            signature_scheme,
            public_key,
        })
    })?;
    rows.collect()
}

fn print_participant(record: &ParticipantInspection) {
    println!("PARTICIPANT");
    println!("participant_id   : {}", record.participant_id);
    println!(
        "label            : {}",
        record.label.as_deref().unwrap_or("-")
    );
    println!("source           : {}", record.source);
    println!("role             : {}", record.role);
    println!("status           : {}", record.lifecycle_status);
    println!();
    println!("totp");
    println!("status           : {}", record.totp_status);
    println!();
    println!("signing");
    println!("status           : {}", record.signing_status);
    println!(
        "signature_scheme : {}",
        record.signature_scheme.as_deref().unwrap_or("-")
    );
    println!(
        "public_key       : {}",
        record.public_key.as_deref().unwrap_or("-")
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
    fn inspection_reports_non_secret_credential_state() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        identity::provision_web_participant_identity(
            &conn,
            "keda-main",
            "human",
            Some("Keda Tsao"),
        )
        .unwrap();

        let empty = inspect_participant(&conn, "keda-main").unwrap().unwrap();
        assert_eq!(empty.role, "user");
        assert_eq!(empty.lifecycle_status, "active");
        assert_eq!(empty.totp_status, "not-configured");
        assert_eq!(empty.signing_status, "not-configured");
        assert_eq!(empty.public_key, None);

        identity::set_web_participant_totp(&conn, "keda-main", &web_auth::generate_totp_secret())
            .unwrap();
        let (_, public_key) = signed_auth::generate_keypair();
        identity::set_web_participant_signing_key(&conn, "keda-main", &public_key).unwrap();

        let active = inspect_participant(&conn, "keda-main").unwrap().unwrap();
        assert_eq!(active.totp_status, "active");
        assert_eq!(active.signing_status, "active");
        assert_eq!(
            active.signature_scheme.as_deref(),
            Some(signed_auth::SIGNATURE_SCHEME)
        );
        assert_eq!(active.public_key.as_deref(), Some(public_key.as_str()));

        identity::set_web_participant_status(&conn, "keda-main", "inactive").unwrap();
        let inactive = inspect_participant(&conn, "keda-main").unwrap().unwrap();
        assert_eq!(inactive.lifecycle_status, "inactive");
        assert_eq!(inactive.totp_status, "active");
        assert_eq!(inactive.signing_status, "active");

        identity::revoke_web_participant_totp(&conn, "keda-main").unwrap();
        identity::revoke_web_participant_signing_key(&conn, "keda-main").unwrap();
        let revoked = inspect_participant(&conn, "keda-main").unwrap().unwrap();
        assert_eq!(revoked.totp_status, "not-configured");
        assert_eq!(revoked.signing_status, "not-configured");
        assert_eq!(revoked.public_key, None);
    }

    #[test]
    fn inspection_lists_participants_and_missing_is_none() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        identity::provision_web_participant_identity(
            &conn,
            "kegui-main",
            "human",
            Some("Kegui Tsao"),
        )
        .unwrap();
        identity::provision_web_participant_identity(
            &conn,
            "keda-main",
            "human",
            Some("Keda Tsao"),
        )
        .unwrap();

        let records = list_participants(&conn).unwrap();
        assert_eq!(records.len(), 2);
        assert_eq!(records[0].participant_id, "keda-main");
        assert_eq!(records[1].participant_id, "kegui-main");
        assert!(inspect_participant(&conn, "missing-main")
            .unwrap()
            .is_none());
    }
}
