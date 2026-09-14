from pathlib import Path

# Cargo: tokio stdio + buffered line I/O.
p = Path('Cargo.toml')
s = p.read_text(encoding='utf-8')
old = 'tokio = { version = "1", features = ["macros", "rt-multi-thread", "net", "signal", "sync"] }'
new = 'tokio = { version = "1", features = ["macros", "rt-multi-thread", "net", "signal", "sync", "io-std", "io-util"] }'
if old not in s:
    raise SystemExit('Cargo tokio feature line not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

# MCP: add stdio server and transport-neutral JSON-RPC value dispatch.
p = Path('src/mcp.rs')
s = p.read_text(encoding='utf-8')
needle = 'use url::Url;\n'
addition = 'use url::Url;\nuse tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};\n'
if needle not in s:
    raise SystemExit('mcp import insertion point not found')
s = s.replace(needle, addition, 1)

needle = '''pub fn app(state: AppState) -> Router {
    Router::new()
        .route(
            "/mcp",
            post(mcp_post)
                .get(mcp_get)
                .delete(mcp_delete)
                .options(mcp_options),
        )
        .with_state(state)
}
'''
addition = needle + '''
/// Serve MCP over newline-delimited JSON-RPC on stdin/stdout.
///
/// Stdout is reserved exclusively for protocol responses. The stdio transport
/// does not create a separate identity model; tool calls retain the canonical
/// Blackboard participant-HMAC authentication and authorization path.
pub async fn serve_stdio(
    state: AppState,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    db::initialize(&state.db_path)?;
    let stdin = tokio::io::stdin();
    let mut lines = BufReader::new(stdin).lines();
    let mut stdout = tokio::io::stdout();

    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }
        let response = match serde_json::from_str::<Value>(&line) {
            Ok(value) => stdio_dispatch(&state, &value).await,
            Err(_) => Some(json!({
                "jsonrpc": "2.0",
                "id": Value::Null,
                "error": {"code": -32700, "message": "parse_error"}
            })),
        };
        if let Some(response) = response {
            let mut encoded = serde_json::to_vec(&response)?;
            encoded.push(b'\\n');
            stdout.write_all(&encoded).await?;
            stdout.flush().await?;
        }
    }
    Ok(())
}

pub(crate) async fn stdio_dispatch(state: &AppState, value: &Value) -> Option<Value> {
    let object = match value.as_object() {
        Some(object) => object,
        None => {
            return Some(json!({
                "jsonrpc": "2.0",
                "id": Value::Null,
                "error": {"code": -32600, "message": "invalid_request"}
            }))
        }
    };
    let id = object.get("id").cloned().unwrap_or(Value::Null);
    if object.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
        return Some(json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": -32600, "message": "invalid_request"}
        }));
    }

    let method = match object.get("method").and_then(Value::as_str) {
        Some(method) => method,
        None if object.contains_key("result") || object.contains_key("error") => return None,
        None => {
            return Some(json!({
                "jsonrpc": "2.0",
                "id": id,
                "error": {"code": -32600, "message": "invalid_request"}
            }))
        }
    };

    // MCP notifications, including notifications/initialized, are one-way.
    if !object.contains_key("id") {
        return None;
    }

    let result = match method {
        "initialize" => match initialize_result(object) {
            Ok(result) => Ok(result),
            Err(message) => Err((-32602, message)),
        },
        "ping" => Ok(json!({})),
        "tools/list" => Ok(tools_list_result()),
        "tools/call" => match tool_call_result(state, object).await {
            Ok(result) => Ok(result),
            Err(message) => Err((-32602, message)),
        },
        _ => Err((-32601, "method_not_found")),
    };

    Some(match result {
        Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
        Err((code, message)) => {
            json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
        }
    })
}
'''
if needle not in s:
    raise SystemExit('mcp app block not found')
s = s.replace(needle, addition, 1)
p.write_text(s, encoding='utf-8')

# CLI: explicit `mcp serve` first-class entry point.
p = Path('src/main.rs')
s = p.read_text(encoding='utf-8')
needle = '''    /// Inspect immutable committed semantic execution evidence.
    Execution {
        #[command(subcommand)]
        command: execution_audit::ExecutionCommand,
    },
'''
addition = needle + '''    /// Serve Conversation Blackboard through the Model Context Protocol.
    Mcp {
        #[command(subcommand)]
        command: McpCommand,
    },
'''
if needle not in s:
    raise SystemExit('CLI enum insertion point not found')
s = s.replace(needle, addition, 1)

needle = '''#[derive(Debug, Default, Args)]
struct RunArgs {
    #[arg(long)]
    host: Option<String>,
    #[arg(long)]
    port: Option<u16>,
    #[arg(long)]
    db: Option<PathBuf>,
}
'''
addition = needle + '''
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
'''
if needle not in s:
    raise SystemExit('RunArgs block not found')
s = s.replace(needle, addition, 1)

needle = '        Some(Command::Execution { command }) => execution_audit::dispatch(command),\n'
addition = needle + '        Some(Command::Mcp { command }) => dispatch_mcp(command),\n'
if needle not in s:
    raise SystemExit('main match insertion point not found')
s = s.replace(needle, addition, 1)

needle = '''fn run_interactive(args: RunArgs) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = RuntimeConfig::from_env_with_overrides(args.host, args.port, args.db);
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    runtime.block_on(runtime::run_server(config, async {
        let _ = tokio::signal::ctrl_c().await;
    }))
}
'''
addition = needle + '''
fn dispatch_mcp(
    command: McpCommand,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
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
'''
if needle not in s:
    raise SystemExit('run_interactive block not found')
s = s.replace(needle, addition, 1)

# Add CLI parse coverage before the first existing CLI unit test.
needle = '''    #[test]
    fn participant_auth_generate_cli_parses() {
'''
addition = '''    #[test]
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

''' + needle
if needle not in s:
    raise SystemExit('CLI test insertion point not found')
s = s.replace(needle, addition, 1)
p.write_text(s, encoding='utf-8')

# Add stdio lifecycle dispatch coverage using the same canonical MCP tool layer.
p = Path('src/mcp_contract_tests.rs')
s = p.read_text(encoding='utf-8')
needle = '''#[tokio::test]
async fn mcp_advertises_hmac_only_auth_contract() {
'''
addition = '''#[tokio::test]
async fn stdio_dispatch_supports_lifecycle_discovery_and_notifications() {
    let fixture = fixture();
    let state = AppState {
        db_path: fixture.db_path.clone(),
        registration_key: None,
    };

    let initialized = mcp::stdio_dispatch(
        &state,
        &json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}
        }),
    )
    .await
    .unwrap();
    assert_eq!(initialized["result"]["protocolVersion"], "2025-11-25");
    assert_eq!(initialized["result"]["serverInfo"]["name"], "conversation-blackboard");

    let notification = mcp::stdio_dispatch(
        &state,
        &json!({"jsonrpc": "2.0", "method": "notifications/initialized"}),
    )
    .await;
    assert!(notification.is_none());

    let listed = mcp::stdio_dispatch(
        &state,
        &json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    )
    .await
    .unwrap();
    assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 6);

    let unknown = mcp::stdio_dispatch(
        &state,
        &json!({"jsonrpc": "2.0", "id": 3, "method": "unknown/method"}),
    )
    .await
    .unwrap();
    assert_eq!(unknown["error"]["code"], -32601);

    let malformed = mcp::stdio_dispatch(&state, &json!({"id": 4, "method": "tools/list"}))
        .await
        .unwrap();
    assert_eq!(malformed["error"]["code"], -32600);
}

''' + needle
if needle not in s:
    raise SystemExit('MCP contract test insertion point not found')
s = s.replace(needle, addition, 1)
p.write_text(s, encoding='utf-8')
