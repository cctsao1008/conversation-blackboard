mod access_api;
#[cfg(test)]
mod access_control_contract_tests;
mod adapter_profile;
mod admin;
mod authorization;
mod client;
mod client_cli;
#[cfg(test)]
mod contract_parity_tests;
mod contract_schema;
mod db;
mod execution;
mod execution_audit;
#[cfg(test)]
mod execution_integrity_tests;
mod github_webhook;
mod grant_admin;
mod history;
mod http;
#[cfg(test)]
mod http_contract_tests;
mod identity;
mod mcp;
#[cfg(test)]
mod mcp_contract_tests;
mod model;
mod oidc;
mod participant_admin;
mod participant_auth;
mod request_auth;
mod runtime;
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
    /// Provision, rotate, or revoke REST bearer identities.
    Identity {
        #[command(subcommand)]
        command: admin::IdentityCommand,
    },
    /// Provision participant identities and manage human TOTP / participant HMAC auth.
    Participant {
        #[command(subcommand)]
        command: participant_admin::ParticipantCommand,
    },
    /// Create, inspect, or revoke delegated authorization grants.
    Grant {
        #[command(subcommand)]
        command: grant_admin::GrantCommand,
    },
    /// Inspect immutable committed semantic execution evidence.
    Execution {
        #[command(subcommand)]
        command: execution_audit::ExecutionCommand,
    },
    /// Serve Conversation Blackboard through the Model Context Protocol.
    Mcp {
        #[command(subcommand)]
        command: McpCommand,
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

#[derive(Debug, Subcommand)]
enum McpCommand {
    /// Serve MCP over stdin/stdout for local MCP-capable clients.
    Serve(McpServeArgs),
}

#[derive(Debug, Args)]
struct McpServeArgs {
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
        Some(Command::Grant { command }) => grant_admin::dispatch(command),
        Some(Command::Execution { command }) => execution_audit::dispatch(command),
        Some(Command::Mcp { command }) => dispatch_mcp(command),
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

fn dispatch_mcp(command: McpCommand) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    match command {
        McpCommand::Serve(args) => {
            let config = RuntimeConfig::from_env_with_overrides(None, None, args.db);
            let state = http::AppState {
                db_path: config.db_path,
                registration_key: config.registration_key,
            };
            let runtime = tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build()?;
            runtime.block_on(mcp::serve_stdio(state))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mcp_stdio_serve_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "mcp",
            "serve",
            "--db",
            "board.db",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Mcp {
                command: McpCommand::Serve(McpServeArgs { db }),
            }) => assert_eq!(db, Some(PathBuf::from("board.db"))),
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn participant_auth_generate_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "auth-generate",
            "--db",
            "board.db",
            "--participant-id",
            "maker-main",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command: participant_admin::ParticipantCommand::AuthGenerate { participant_id, .. },
            }) => assert_eq!(participant_id, "maker-main"),
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn participant_auth_rotate_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "auth-rotate",
            "--db",
            "board.db",
            "--participant-id",
            "maker-main",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command: participant_admin::ParticipantCommand::AuthRotate { participant_id, .. },
            }) => assert_eq!(participant_id, "maker-main"),
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn participant_totp_enroll_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "totp-enroll",
            "--db",
            "board.db",
            "--participant-id",
            "cheng-main",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command: participant_admin::ParticipantCommand::TotpEnroll { participant_id, .. },
            }) => assert_eq!(participant_id, "cheng-main"),
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn participant_show_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "show",
            "--db",
            "board.db",
            "--participant-id",
            "keda-main",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command: participant_admin::ParticipantCommand::Show { participant_id, .. },
            }) => assert_eq!(participant_id, "keda-main"),
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn participant_list_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "list",
            "--db",
            "board.db",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command: participant_admin::ParticipantCommand::List { .. },
            }) => {}
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn participant_deactivate_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "deactivate",
            "--db",
            "board.db",
            "--participant-id",
            "mcp-smoke-main",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command: participant_admin::ParticipantCommand::Deactivate { participant_id, .. },
            }) => assert_eq!(participant_id, "mcp-smoke-main"),
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn participant_reactivate_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "reactivate",
            "--db",
            "board.db",
            "--participant-id",
            "mcp-smoke-main",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command: participant_admin::ParticipantCommand::Reactivate { participant_id, .. },
            }) => assert_eq!(participant_id, "mcp-smoke-main"),
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn participant_set_owner_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "participant",
            "set-owner",
            "--db",
            "board.db",
            "--participant-id",
            "maker-main",
            "--provider",
            "github",
            "--subject",
            "543608",
            "--login",
            "cctsao1008",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Participant {
                command:
                    participant_admin::ParticipantCommand::SetOwner {
                        participant_id,
                        provider,
                        subject,
                        ..
                    },
            }) => {
                assert_eq!(participant_id, "maker-main");
                assert_eq!(provider, "github");
                assert_eq!(subject, "543608");
            }
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn delegated_grant_create_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "create",
            "--db",
            "board.db",
            "--principal-provider",
            "oidc:https://issuer.example",
            "--principal-subject",
            "agent-1",
            "--participant-id",
            "maker-main",
            "--capability",
            "post_message",
            "--resource",
            "control-systems",
            "--intent-id",
            "intent-79",
            "--expires-at",
            "2000000000",
            "--one-shot",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Grant {
                command:
                    grant_admin::GrantCommand::Create {
                        participant_id,
                        one_shot,
                        ..
                    },
            }) => {
                assert_eq!(participant_id, "maker-main");
                assert!(one_shot);
            }
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn authorization_grant_verify_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "verify",
            "--db",
            "board.db",
        ])
        .unwrap();
        assert!(matches!(
            cli.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::Verify { .. }
            })
        ));
    }

    #[test]
    fn durable_principal_grant_cli_parses() {
        let create = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "durable",
            "create",
            "--db",
            "board.db",
            "--principal-provider",
            "oidc:https://issuer.example",
            "--principal-subject",
            "agent-1",
            "--participant-id",
            "maker-main",
            "--capability",
            "post_message",
            "--resource",
            "control-systems",
        ])
        .unwrap();
        assert!(matches!(
            create.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::Durable {
                    command: grant_admin::DurableGrantCommand::Create { .. }
                }
            })
        ));

        let list = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "durable",
            "list",
            "--db",
            "board.db",
            "--participant-id",
            "maker-main",
        ])
        .unwrap();
        assert!(matches!(
            list.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::Durable {
                    command: grant_admin::DurableGrantCommand::List { .. }
                }
            })
        ));

        let deactivate = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "durable",
            "deactivate",
            "--db",
            "board.db",
            "--grant-id",
            "7",
        ])
        .unwrap();
        assert!(matches!(
            deactivate.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::Durable {
                    command: grant_admin::DurableGrantCommand::Deactivate { grant_id: 7, .. }
                }
            })
        ));
    }

    #[test]
    fn execution_audit_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "execution",
            "audit",
            "--db",
            "board.db",
            "--participant-id",
            "maker-main",
            "--intent-id",
            "intent-83",
        ])
        .unwrap();
        assert!(matches!(
            cli.command,
            Some(Command::Execution {
                command: execution_audit::ExecutionCommand::Audit { .. }
            })
        ));
    }

    #[test]
    fn execution_verify_audit_cli_parses() {
        let cli = Cli::try_parse_from([
            "conversation-blackboard",
            "execution",
            "verify-audit",
            "--db",
            "board.db",
            "--participant-id",
            "maker-main",
            "--intent-id",
            "intent-85",
        ])
        .unwrap();

        match cli.command {
            Some(Command::Execution {
                command:
                    execution_audit::ExecutionCommand::VerifyAudit {
                        participant_id,
                        intent_id,
                        ..
                    },
            }) => {
                assert_eq!(participant_id, "maker-main");
                assert_eq!(intent_id, "intent-85");
            }
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn delegated_grant_list_and_deactivate_cli_parse() {
        let list = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "list",
            "--db",
            "board.db",
            "--participant-id",
            "maker-main",
        ])
        .unwrap();
        assert!(matches!(
            list.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::List { .. }
            })
        ));

        let deactivate = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "deactivate",
            "--db",
            "board.db",
            "--grant-id",
            "7",
        ])
        .unwrap();
        assert!(matches!(
            deactivate.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::Deactivate { grant_id: 7, .. }
            })
        ));
    }
}
