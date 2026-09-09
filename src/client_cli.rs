use std::{env, error::Error, time::Duration};

use clap::{Args, Subcommand};

use crate::client::BlackboardClient;

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Args)]
pub struct ClientArgs {
    /// Board base URL; defaults to BLACKBOARD_URL or localhost.
    #[arg(long)]
    url: Option<String>,
    /// Request timeout in seconds.
    #[arg(long, default_value_t = 5.0)]
    timeout: f64,
    #[command(subcommand)]
    command: ClientCommand,
}

#[derive(Debug, Subcommand)]
enum ClientCommand {
    /// Check the unauthenticated health endpoint.
    Health,
    /// Resolve the current server-controlled identity.
    Whoami,
    /// List channels visible to the current identity.
    Channels,
    /// Read messages after a global cursor.
    Read {
        #[arg(long, default_value_t = 0)]
        after: i64,
        #[arg(long)]
        channel: Option<String>,
        #[arg(long, default_value_t = 100)]
        limit: usize,
    },
    /// Post a message or reply.
    Post {
        #[arg(long)]
        channel: String,
        #[arg(long, default_value = "message")]
        kind: String,
        #[arg(long)]
        body: String,
        #[arg(long)]
        reply_to: Option<i64>,
    },
}

pub fn dispatch(args: ClientArgs) -> DynResult {
    if !args.timeout.is_finite() || args.timeout <= 0.0 {
        return Err("--timeout must be > 0".into());
    }
    let url = args
        .url
        .or_else(|| env::var("BLACKBOARD_URL").ok())
        .unwrap_or_else(|| "http://127.0.0.1:8766".to_owned());
    let token = env::var("BLACKBOARD_TOKEN").map_err(|_| "missing BLACKBOARD_TOKEN")?;
    let client = BlackboardClient::new(&url, token, Duration::from_secs_f64(args.timeout))?;

    match args.command {
        ClientCommand::Health => {
            client.health()?;
            print_json(&serde_json::json!({"status": "ok"}))
        }
        ClientCommand::Whoami => print_json(&client.whoami()?),
        ClientCommand::Channels => print_json(&client.channels()?),
        ClientCommand::Read {
            after,
            channel,
            limit,
        } => print_json(&client.messages(after, channel.as_deref(), limit)?),
        ClientCommand::Post {
            channel,
            kind,
            body,
            reply_to,
        } => print_json(&client.post(&channel, &kind, &body, reply_to)?),
    }
}

fn print_json<T: serde::Serialize>(value: &T) -> DynResult {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}
