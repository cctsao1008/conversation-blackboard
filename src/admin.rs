use std::{
    env,
    error::Error,
    fs,
    path::{Path, PathBuf},
    time::Duration,
};

use clap::{Args, Subcommand};
use reqwest::blocking::{Client, Response};
use rusqlite::Connection;
use serde_json::Value;
use url::Url;

use crate::{db, identity};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Subcommand)]
pub enum DbCommand {
    /// Initialize a database using the embedded canonical schema.
    Init {
        #[arg(long)]
        db: PathBuf,
    },
    /// Run SQLite PRAGMA integrity_check.
    Integrity {
        #[arg(long)]
        db: PathBuf,
    },
    /// Create a consistent integrity-checked SQLite backup.
    Backup {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Restore an integrity-checked backup. Stop the service before restoring.
    Restore {
        #[arg(long)]
        backup: PathBuf,
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        force: bool,
    },
}

#[derive(Debug, Subcommand)]
pub enum IdentityCommand {
    /// Create a new identity and print its bearer token once.
    Provision {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        source: String,
        #[arg(long)]
        label: Option<String>,
    },
    /// Rotate the bearer token for one or more existing instances.
    Rotate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long, required = true)]
        instance: Vec<String>,
    },
    /// Revoke the bearer token for one or more existing instances.
    Revoke {
        #[arg(long)]
        db: PathBuf,
        #[arg(long, required = true)]
        instance: Vec<String>,
    },
}

#[derive(Debug, Subcommand)]
pub enum VerifyCommand {
    /// Verify health, identity, and message reads without printing the bearer token.
    Endpoint(VerifyEndpointArgs),
}

#[derive(Debug, Args)]
pub struct VerifyEndpointArgs {
    /// Board base URL. Defaults to BLACKBOARD_URL.
    #[arg(long)]
    url: Option<String>,
    #[arg(long)]
    expect_source: Option<String>,
    #[arg(long)]
    expect_instance: Option<String>,
    #[arg(long)]
    channel: Option<String>,
    #[arg(long, default_value_t = 0)]
    after: i64,
    #[arg(long, default_value_t = 20)]
    limit: usize,
    #[arg(long, default_value_t = 5.0)]
    timeout: f64,
}

pub fn dispatch_db(command: DbCommand) -> DynResult {
    match command {
        DbCommand::Init { db: path } => {
            ensure_parent(&path)?;
            db::initialize(&path)?;
            println!("Database initialized: {}", path.display());
            Ok(())
        }
        DbCommand::Integrity { db: path } => {
            require_file(&path, "database")?;
            if integrity_ok(&path)? {
                println!("ok");
                Ok(())
            } else {
                Err(format!("database integrity check failed: {}", path.display()).into())
            }
        }
        DbCommand::Backup { db: path, out } => backup_database(&path, &out),
        DbCommand::Restore {
            backup,
            db: path,
            force,
        } => restore_database(&backup, &path, force),
    }
}

pub fn dispatch_identity(command: IdentityCommand) -> DynResult {
    match command {
        IdentityCommand::Provision {
            db: path,
            source,
            label,
        } => {
            require_file(&path, "database")?;
            let conn = db::connect(&path)?;
            let (identity, token) = identity::register_identity(&conn, &source, label.as_deref())?;
            print_identity_token(&identity.source, &identity.instance, identity.label.as_deref(), &token);
            Ok(())
        }
        IdentityCommand::Rotate { db: path, instance } => {
            require_file(&path, "database")?;
            let conn = db::connect(&path)?;
            for id in instance {
                let record = identity::get_identity(&conn, &id)?
                    .ok_or_else(|| format!("unknown instance: {id}"))?;
                let token = identity::rotate_token(&conn, &id)?
                    .ok_or_else(|| format!("unknown instance: {id}"))?;
                print_identity_token(&record.source, &record.instance, record.label.as_deref(), &token);
            }
            Ok(())
        }
        IdentityCommand::Revoke { db: path, instance } => {
            require_file(&path, "database")?;
            let conn = db::connect(&path)?;
            for id in instance {
                if !identity::revoke_token(&conn, &id)? {
                    return Err(format!("unknown instance: {id}").into());
                }
                println!("revoked: {id}");
            }
            Ok(())
        }
    }
}

pub fn dispatch_verify(command: VerifyCommand) -> DynResult {
    match command {
        VerifyCommand::Endpoint(args) => verify_endpoint(args),
    }
}

fn print_identity_token(source: &str, instance: &str, label: Option<&str>, token: &str) {
    println!("SAVE THIS TOKEN NOW. Only its SHA-256 hash is stored in the database.");
    println!("source  : {source}");
    println!("instance: {instance}");
    println!("label   : {}", label.unwrap_or(""));
    println!("token   : {token}");
}

fn backup_database(source: &Path, output: &Path) -> DynResult {
    require_file(source, "source database")?;
    ensure_parent(output)?;
    let tmp = sibling_with_suffix(output, ".tmp");
    remove_database_files(&tmp)?;

    let conn = db::connect(source)?;
    vacuum_into(&conn, &tmp)?;
    drop(conn);

    if !integrity_ok(&tmp)? {
        remove_database_files(&tmp)?;
        return Err("backup integrity check failed".into());
    }

    if output.exists() {
        fs::remove_file(output)?;
    }
    fs::rename(&tmp, output)?;
    println!("Backup created: {}", output.display());
    Ok(())
}

fn restore_database(backup: &Path, target: &Path, force: bool) -> DynResult {
    require_file(backup, "backup")?;
    if target.exists() && !force {
        return Err(format!("target exists: {} (use --force to overwrite)", target.display()).into());
    }
    if !integrity_ok(backup)? {
        return Err("backup integrity check failed".into());
    }

    ensure_parent(target)?;
    let tmp = sibling_with_suffix(target, ".restore-tmp");
    remove_database_files(&tmp)?;

    let source = Connection::open(backup)?;
    vacuum_into(&source, &tmp)?;
    drop(source);

    if !integrity_ok(&tmp)? {
        remove_database_files(&tmp)?;
        return Err("restored database integrity check failed".into());
    }

    if force {
        remove_database_files(target)?;
    }
    fs::rename(&tmp, target)?;
    println!("Database restored: {}", target.display());
    Ok(())
}

fn vacuum_into(conn: &Connection, target: &Path) -> DynResult {
    let target = target
        .to_str()
        .ok_or("database path is not valid Unicode")?;
    conn.execute("VACUUM INTO ?1", [target])?;
    Ok(())
}

fn integrity_ok(path: &Path) -> DynResult<bool> {
    require_file(path, "database")?;
    let conn = Connection::open(path)?;
    let result: String = conn.query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
    Ok(result == "ok")
}

fn verify_endpoint(args: VerifyEndpointArgs) -> DynResult {
    if args.after < 0 {
        return Err("--after must be >= 0".into());
    }
    if !(1..=200).contains(&args.limit) {
        return Err("--limit must be between 1 and 200".into());
    }
    if !args.timeout.is_finite() || args.timeout <= 0.0 {
        return Err("--timeout must be > 0".into());
    }

    let raw_url = args
        .url
        .or_else(|| env::var("BLACKBOARD_URL").ok())
        .ok_or("missing --url or BLACKBOARD_URL")?;
    let token = env::var("BLACKBOARD_TOKEN").map_err(|_| "missing BLACKBOARD_TOKEN")?;
    if token.is_empty() {
        return Err("BLACKBOARD_TOKEN is empty".into());
    }

    let base = normalized_base_url(&raw_url)?;
    let client = Client::builder()
        .timeout(Duration::from_secs_f64(args.timeout))
        .build()?;

    let health = json_response(client.get(base.join("api/health")?).send()?)?;
    if health.get("status").and_then(Value::as_str) != Some("ok") {
        return Err("unexpected health response".into());
    }
    println!("PASS: health");

    let whoami = json_response(
        client
            .get(base.join("api/whoami")?)
            .bearer_auth(&token)
            .send()?,
    )?;
    let source = whoami
        .get("source")
        .and_then(Value::as_str)
        .ok_or("whoami response missing source")?;
    let instance = whoami
        .get("instance")
        .and_then(Value::as_str)
        .ok_or("whoami response missing instance")?;
    let label = whoami.get("label").and_then(Value::as_str).unwrap_or("");

    if let Some(expected) = args.expect_source.as_deref() {
        if source != expected {
            return Err(format!("source mismatch: expected {expected}, got {source}").into());
        }
    }
    if let Some(expected) = args.expect_instance.as_deref() {
        if instance != expected {
            return Err(format!("instance mismatch: expected {expected}, got {instance}").into());
        }
    }
    println!("PASS: whoami source={source} instance={instance} label={label}");

    let mut messages_url = base.join("api/messages")?;
    {
        let mut query = messages_url.query_pairs_mut();
        query.append_pair("after", &args.after.to_string());
        query.append_pair("limit", &args.limit.to_string());
        if let Some(channel) = args.channel.as_deref() {
            query.append_pair("channel", channel);
        }
    }
    let messages = json_response(client.get(messages_url).bearer_auth(&token).send()?)?;
    let list = messages
        .get("messages")
        .and_then(Value::as_array)
        .ok_or("messages response missing messages array")?;
    println!(
        "PASS: messages count={} after={} channel={}",
        list.len(),
        args.after,
        args.channel.as_deref().unwrap_or("*")
    );
    if let Some(latest) = list.last().and_then(|message| message.get("id")).and_then(Value::as_i64) {
        println!("latest_id={latest}");
    }
    Ok(())
}

fn json_response(response: Response) -> DynResult<Value> {
    let status = response.status();
    if !status.is_success() {
        return Err(format!("endpoint returned HTTP {}", status.as_u16()).into());
    }
    Ok(response.json()?)
}

fn normalized_base_url(raw: &str) -> DynResult<Url> {
    let mut normalized = raw.trim().trim_end_matches('/').to_owned();
    normalized.push('/');
    let url = Url::parse(&normalized)?;
    match url.scheme() {
        "http" | "https" => Ok(url),
        _ => Err("endpoint URL must use http or https".into()),
    }
}

fn ensure_parent(path: &Path) -> DynResult {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)?;
        }
    }
    Ok(())
}

fn require_file(path: &Path, what: &str) -> DynResult {
    if path.is_file() {
        Ok(())
    } else {
        Err(format!("{what} does not exist: {}", path.display()).into())
    }
}

fn sibling_with_suffix(path: &Path, suffix: &str) -> PathBuf {
    let name = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| "board.db".to_owned());
    path.with_file_name(format!("{name}{suffix}"))
}

fn remove_database_files(path: &Path) -> DynResult {
    for candidate in [
        path.to_path_buf(),
        PathBuf::from(format!("{}-wal", path.display())),
        PathBuf::from(format!("{}-shm", path.display())),
    ] {
        if candidate.exists() {
            fs::remove_file(candidate)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Identity;
    use tempfile::tempdir;

    #[test]
    fn backup_restore_preserves_messages_and_token_hashes() {
        let dir = tempdir().unwrap();
        let source_path = dir.path().join("source.db");
        let backup_path = dir.path().join("backup.db");
        let restored_path = dir.path().join("restored.db");

        db::initialize(&source_path).unwrap();
        let conn = db::connect(&source_path).unwrap();
        let (identity_record, token) = identity::register_identity(&conn, "test-source", Some("test")).unwrap();
        let message = db::append_message(
            &conn,
            &Identity {
                source: identity_record.source.clone(),
                instance: identity_record.instance.clone(),
                label: identity_record.label.clone(),
            },
            "test-channel",
            "message",
            "hello",
            None,
        )
        .unwrap();
        drop(conn);

        backup_database(&source_path, &backup_path).unwrap();
        restore_database(&backup_path, &restored_path, false).unwrap();

        assert!(integrity_ok(&restored_path).unwrap());
        let restored = db::connect(&restored_path).unwrap();
        assert_eq!(
            identity::resolve_identity(&restored, &token)
                .unwrap()
                .unwrap()
                .instance,
            identity_record.instance
        );
        let messages = db::list_messages_after(&restored, 0, Some("test-channel"), 20).unwrap();
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0].id, message.id);
        assert_eq!(messages[0].body, "hello");
    }

    #[test]
    fn restore_refuses_overwrite_without_force() {
        let dir = tempdir().unwrap();
        let source_path = dir.path().join("source.db");
        let backup_path = dir.path().join("backup.db");
        let target_path = dir.path().join("target.db");
        db::initialize(&source_path).unwrap();
        backup_database(&source_path, &backup_path).unwrap();
        db::initialize(&target_path).unwrap();
        assert!(restore_database(&backup_path, &target_path, false).is_err());
        restore_database(&backup_path, &target_path, true).unwrap();
        assert!(integrity_ok(&target_path).unwrap());
    }

    #[test]
    fn verifier_accepts_http_and_https_only() {
        assert!(normalized_base_url("http://127.0.0.1:8766").is_ok());
        assert!(normalized_base_url("https://board.example.test/").is_ok());
        assert!(normalized_base_url("file:///tmp/board").is_err());
    }
}
