use std::{env, future::Future, net::SocketAddr, path::PathBuf};

use crate::{access_api, db, github_webhook, history, http, mcp, oidc};
use http::AppState;

#[derive(Clone, Debug)]
pub struct RuntimeConfig {
    pub host: String,
    pub port: u16,
    pub db_path: PathBuf,
    pub registration_key: Option<String>,
    pub mcp_resource_url: Option<String>,
}

impl RuntimeConfig {
    pub fn from_env_with_overrides(
        host: Option<String>,
        port: Option<u16>,
        db_path: Option<PathBuf>,
    ) -> Self {
        let host = host
            .or_else(|| env::var("BLACKBOARD_HOST").ok())
            .unwrap_or_else(|| "127.0.0.1".to_owned());
        let port = port
            .or_else(|| {
                env::var("BLACKBOARD_PORT")
                    .ok()
                    .and_then(|value| value.parse::<u16>().ok())
            })
            .unwrap_or(8766);
        let db_path = db_path
            .or_else(|| env::var("BLACKBOARD_DB").ok().map(PathBuf::from))
            .unwrap_or_else(|| PathBuf::from("board.db"));
        let registration_key = env::var("BLACKBOARD_REGISTRATION_KEY")
            .ok()
            .filter(|value| !value.is_empty());
        let mcp_resource_url = env::var("BLACKBOARD_MCP_RESOURCE_URL")
            .ok()
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty());

        Self {
            host,
            port,
            db_path,
            registration_key,
            mcp_resource_url,
        }
    }
}

pub async fn run_server<F>(
    config: RuntimeConfig,
    shutdown: F,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>>
where
    F: Future<Output = ()> + Send + 'static,
{
    db::initialize(&config.db_path)?;
    let conn = db::connect(&config.db_path)?;
    github_webhook::ensure_owner_columns(&conn)?;
    drop(conn);

    let addr: SocketAddr = format!("{}:{}", config.host, config.port).parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    let state = AppState {
        db_path: config.db_path.clone(),
        registration_key: config.registration_key,
    };
    let github_state = github_webhook::GithubWebhookState::from_env(config.db_path.clone());
    let oidc_state = oidc::OidcState::from_env(config.db_path).await?;
    let oidc_verifier = oidc_state.as_ref().map(oidc::OidcState::verifier);
    let mut app = http::app(state.clone())
        .merge(history::app(state.clone()))
        .merge(access_api::app(state.clone()))
        .merge(mcp::app_with_remote_auth(
            state,
            oidc_verifier,
            config.mcp_resource_url.as_deref(),
        )?)
        .merge(github_webhook::app(github_state));
    if let Some(oidc_state) = oidc_state {
        app = app.merge(oidc::app(oidc_state));
    }

    println!("conversation-blackboard listening on http://{addr}");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown)
        .await?;
    Ok(())
}
