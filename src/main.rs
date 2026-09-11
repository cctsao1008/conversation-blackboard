mod admin;
mod client;
mod client_cli;
mod db;
mod history;
mod http;
#[cfg(test)]
mod http_contract_tests;
mod identity;
mod mcp;
#[cfg(test)]
mod mcp_contract_tests;
mod model;
mod participant_admin;
mod participant_key;
mod request_auth;
mod runtime;
mod signed_auth;
mod web_admin;
mod web_auth;
#[cfg(test)]
mod web_auth_contract_tests;
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
    /// Provision, rotate, or revoke REST conversation identities.
    Identity {
        #[command(subcommand)]
        command: admin::IdentityCommand,
    },
    /// Generate optional Participant ID key material without registering it.
    Participant {
        #[command(subcommand)]
        command: participant_admin::ParticipantCommand,
    },
    /// Provision, rotate, or revoke web Participant IDs and prompt-held keys.
    Web {
        #[command(subcommand)]
        command: web_admin::WebCommand,
    },
    /// Verify a local or public blackboard endpoint.
    Verify {
        #[command(subcommand)]
        command: admin::VerifyCommand,
    },
    /// Use the vendor-neutral Rust client/adapter against a board.
    Client(client_cli::ClientArgs),
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
        Some(Command::Participant { command }) => participant_admin::dispatch(command),
        Some(Command::Web { command }) => web_admin::dispatch(command),
        Some(Command::Verify { command }) => admin::dispatch_verify(command),
        Some(Command::Client(args)) => client_cli::dispatch(args),
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn participant_generate_cli_parses_mini_rsa() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "generate",
            "--scheme",
            "mini-rsa",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command:
                    participant_admin::ParticipantCommand::Generate {
                        scheme: participant_admin::GeneratorScheme::MiniRsa,
                    },
            }) => {}
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn participant_generate_cli_parses_ed25519() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "generate",
            "--scheme",
            "ed25519",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command:
                    participant_admin::ParticipantCommand::Generate {
                        scheme: participant_admin::GeneratorScheme::Ed25519,
                    },
            }) => {}
            other => panic!("unexpected command: {other:?}"),
        }
    }
}
