mod admin;
mod db;
mod http;
mod identity;
mod model;
mod runtime;
#[cfg(windows)]
mod windows_service;

use std::path::PathBuf;

use clap::{Args, Parser, Subcommand};
use runtime::RuntimeConfig;

#[derive(Debug, Parser)]
#[command(name = "conversation-blackboard")]
#[command(about = "Persistent blackboard for independent conversations")]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Run the HTTP server interactively.
    Run(RunArgs),
    /// Database initialization, integrity, backup, and restore operations.
    Db {
        #[command(subcommand)]
        command: admin::DbCommand,
    },
    /// Provision, rotate, or revoke conversation identities.
    Identity {
        #[command(subcommand)]
        command: admin::IdentityCommand,
    },
    /// Verify a local or public blackboard endpoint.
    Verify {
        #[command(subcommand)]
        command: admin::VerifyCommand,
    },
    /// Install or control the native Windows service.
    #[cfg(windows)]
    Service {
        #[command(subcommand)]
        command: windows_service::ServiceCommand,
    },
}

#[derive(Debug, Default, Args)]
struct RunArgs {
    #[arg(long)]
    host: Option<String>,
    #[arg(long)]
    port: Option<u16>,
    #[arg(long)]
    db: Option<PathBuf>,
}

fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let cli = Cli::parse();
    match cli.command {
        None => run_interactive(RunArgs::default()),
        Some(Command::Run(args)) => run_interactive(args),
        Some(Command::Db { command }) => admin::dispatch_db(command),
        Some(Command::Identity { command }) => admin::dispatch_identity(command),
        Some(Command::Verify { command }) => admin::dispatch_verify(command),
        #[cfg(windows)]
        Some(Command::Service { command }) => windows_service::dispatch(command),
    }
}

fn run_interactive(args: RunArgs) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = RuntimeConfig::from_env_with_overrides(args.host, args.port, args.db);
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    runtime.block_on(runtime::run_server(config, async {
        let _ = tokio::signal::ctrl_c().await;
    }))
}
