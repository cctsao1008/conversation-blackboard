use std::{error::Error, path::PathBuf};

use clap::Subcommand;

use crate::{db, identity, signed_auth, web_auth};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Subcommand)]
pub enum WebCommand {
    /// Provision a participant identity for human TOTP and/or agent signing.
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
    /// Generate and register a TOTP secret for human web login.
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

pub fn dispatch(command: WebCommand) -> DynResult {
    match command {
        WebCommand::Provision {
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
            Ok(())
        }
        WebCommand::TotpEnroll {
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
            println!("otpauth_uri     : {uri}");
            println!("note            : Add this account to any RFC 6238 authenticator, then use the 6-digit code on the web UI.");
            Ok(())
        }
        WebCommand::TotpRevoke {
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
        WebCommand::SetSigningKey {
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
        WebCommand::RevokeSigningKey {
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

fn require_database(path: &std::path::Path) -> DynResult {
    if path.is_file() {
        Ok(())
    } else {
        Err(format!("database does not exist: {}", path.display()).into())
    }
}
