mod db;
mod http;
mod identity;
mod model;

use std::{env, net::SocketAddr, path::PathBuf};

use http::AppState;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let host = env::var("BLACKBOARD_HOST").unwrap_or_else(|_| "127.0.0.1".to_owned());
    let port = env::var("BLACKBOARD_PORT")
        .ok()
        .and_then(|value| value.parse::<u16>().ok())
        .unwrap_or(8766);
    let db_path = PathBuf::from(env::var("BLACKBOARD_DB").unwrap_or_else(|_| "board.db".to_owned()));
    let registration_key = env::var("BLACKBOARD_REGISTRATION_KEY").ok();

    db::initialize(&db_path)?;

    let addr: SocketAddr = format!("{host}:{port}").parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    let state = AppState {
        db_path,
        registration_key,
    };

    println!("conversation-blackboard listening on http://{addr}");
    axum::serve(listener, http::app(state))
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    Ok(())
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
}
