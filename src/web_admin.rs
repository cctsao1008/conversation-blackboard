use std::{error::Error, path::PathBuf};

use clap::Subcommand;

use crate::{db, identity};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Subcommand)]
pub enum WebCommand {
    /// Provision the first web-navigation capability for an existing identity.
    Provision {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        instance: String,
    },
    /// Rotate the web-navigation capability for an existing identity.
    Rotate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        instance: String,
    },
    /// Revoke the web-navigation capability for an existing identity.
    Revoke {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        instance: String,
    },
}

pub fn dispatch(command: WebCommand) -> DynResult {
    match command {
        WebCommand::Provision { db: path, instance } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let record = identity::get_identity(&conn, &instance)?
                .ok_or_else(|| format!("unknown instance: {instance}"))?;
            let capability = identity::provision_web_capability(&conn, &instance)?
                .ok_or_else(|| format!("active web capability already exists for {instance}; use web rotate"))?;
            print_capability(&record.source, &record.instance, &capability);
            Ok(())
        }
        WebCommand::Rotate { db: path, instance } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let record = identity::get_identity(&conn, &instance)?
                .ok_or_else(|| format!("unknown instance: {instance}"))?;
            let capability = identity::rotate_web_capability(&conn, &instance)?
                .ok_or_else(|| format!("unknown instance: {instance}"))?;
            print_capability(&record.source, &record.instance, &capability);
            Ok(())
        }
        WebCommand::Revoke { db: path, instance } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if identity::get_identity(&conn, &instance)?.is_none() {
                return Err(format!("unknown instance: {instance}").into());
            }
            if !identity::revoke_web_capability(&conn, &instance)? {
                return Err(format!("no active web capability for {instance}").into());
            }
            println!("revoked web capability: {instance}");
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

fn print_capability(source: &str, instance: &str, capability: &str) {
    println!("SAVE THIS WEB CAPABILITY NOW. Only its SHA-256 hash is stored in the database.");
    println!("source     : {source}");
    println!("instance   : {instance}");
    println!("capability : {capability}");
}
