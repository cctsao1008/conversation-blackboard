use std::{error::Error, path::PathBuf};

use clap::Subcommand;

use crate::{db, identity, signed_auth};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Subcommand)]
pub enum WebCommand {
    /// Provision a web participant with a prompt-held private key.
    Provision {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        source: String,
        #[arg(long)]
        label: Option<String>,
        /// Use caller-supplied key material instead of generating a key.
        #[arg(long)]
        key: Option<String>,
    },
    /// Rotate the prompt-held private key for a web participant.
    Rotate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        /// Use caller-supplied key material instead of generating a key.
        #[arg(long)]
        key: Option<String>,
    },
    /// Revoke the prompt-held private key for a web participant.
    Revoke {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
    /// Register or rotate the Ed25519 public signing key for a participant.
    SetSigningKey {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        public_key: String,
    },
    /// Revoke the Ed25519 public signing key for a participant.
    RevokeSigningKey {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
}

pub fn dispatch(command: WebCommand) -> DynResult {
    match command {
        WebCommand::Provision {
            db: path,
            participant_id,
            source,
            label,
            key,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let private_key = key.unwrap_or_else(identity::new_web_private_key);
            let record = identity::provision_web_participant(
                &conn,
                &participant_id,
                &source,
                label.as_deref(),
                &private_key,
            )?
            .ok_or_else(|| format!("web participant already exists: {participant_id}"))?;
            print_participant(&record.source, &record.instance, &private_key);
            Ok(())
        }
        WebCommand::Rotate {
            db: path,
            participant_id,
            key,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let record = identity::get_web_participant(&conn, &participant_id)?
                .ok_or_else(|| format!("unknown web participant: {participant_id}"))?;
            let private_key = key.unwrap_or_else(identity::new_web_private_key);
            if !identity::rotate_web_participant_key(&conn, &participant_id, &private_key)? {
                return Err(format!("unknown web participant: {participant_id}").into());
            }
            print_participant(&record.source, &record.instance, &private_key);
            Ok(())
        }
        WebCommand::Revoke {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if identity::get_web_participant(&conn, &participant_id)?.is_none() {
                return Err(format!("unknown web participant: {participant_id}").into());
            }
            if !identity::revoke_web_participant_key(&conn, &participant_id)? {
                return Err(format!("no active key for web participant: {participant_id}").into());
            }
            println!("revoked web participant key: {participant_id}");
            Ok(())
        }
        WebCommand::SetSigningKey {
            db: path,
            participant_id,
            public_key,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if identity::get_web_participant(&conn, &participant_id)?.is_none() {
                return Err(format!("unknown web participant: {participant_id}").into());
            }
            if !signed_auth::validate_public_key(&public_key) {
                return Err("invalid Ed25519 public key material".into());
            }
            if !identity::set_web_participant_signing_key(&conn, &participant_id, &public_key)? {
                return Err(format!("unknown web participant: {participant_id}").into());
            }
            println!("PARTICIPANT SIGNING KEY READY");
            println!("participant_id   : {participant_id}");
            println!("signature_scheme : {}", signed_auth::SIGNATURE_SCHEME);
            println!("public_key       : {public_key}");
            Ok(())
        }
        WebCommand::RevokeSigningKey {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if identity::get_web_participant(&conn, &participant_id)?.is_none() {
                return Err(format!("unknown web participant: {participant_id}").into());
            }
            if !identity::revoke_web_participant_signing_key(&conn, &participant_id)? {
                return Err(
                    format!("no active signing key for web participant: {participant_id}").into(),
                );
            }
            println!("revoked participant signing key: {participant_id}");
            Ok(())
        }
    }
}

fn require_database(path: &std::path::Path) -> DynResult {
    if path.is_file() {
        Ok(())
    } else {
        Err(format!("database does not exist: {}", path.display()).into())
    }
}

fn print_participant(source: &str, participant_id: &str, private_key: &str) {
    println!("WEB PARTICIPANT READY. Copy the ID and key into the conversation prompt/context.");
    println!("source         : {source}");
    println!("participant_id : {participant_id}");
    println!("private_key    : {private_key}");
}
