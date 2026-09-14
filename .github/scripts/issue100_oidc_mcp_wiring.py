from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# Share the existing OIDC verifier without changing its verification semantics.
oidc = Path("src/oidc.rs")
s = oidc.read_text(encoding="utf-8")
s = replace_once(s, "struct OidcVerifier {", "pub(crate) struct OidcVerifier {", "OidcVerifier visibility")
s = replace_once(s, "    fn verify(&self, token: &str) -> Result<execution::Principal, ()> {", "    pub(crate) fn verify(&self, token: &str) -> Result<execution::Principal, ()> {", "OidcVerifier::verify visibility")
anchor = "}\n\nimpl OidcVerifier {"
insert = "}\n\nimpl OidcState {\n    pub(crate) fn verifier(&self) -> Arc<OidcVerifier> {\n        self.verifier.clone()\n    }\n}\n\nimpl OidcVerifier {"
s = replace_once(s, anchor, insert, "OidcState verifier accessor")
oidc.write_text(s, encoding="utf-8")

# Give the HTTP MCP adapter an optional shared verifier while keeping the
# existing app(AppState) entry point for tests and HMAC-only deployments.
mcp = Path("src/mcp.rs")
s = mcp.read_text(encoding="utf-8")
s = replace_once(s, "use std::sync::OnceLock;", "use std::sync::{Arc, OnceLock};", "mcp Arc import")
s = replace_once(
    s,
    "    model::Identity, participant_auth,\n};",
    "    model::Identity, oidc, participant_auth,\n};",
    "mcp oidc module import",
)
old_app = '''pub fn app(state: AppState) -> Router {\n    Router::new()\n        .route(\n            \"/mcp\",\n            post(mcp_post)\n                .get(mcp_get)\n                .delete(mcp_delete)\n                .options(mcp_options),\n        )\n        .with_state(state)\n}\n'''
new_app = '''#[derive(Clone)]\nstruct McpHttpState {\n    app: AppState,\n    _oidc_verifier: Option<Arc<oidc::OidcVerifier>>,\n}\n\npub fn app(state: AppState) -> Router {\n    app_with_oidc(state, None)\n}\n\npub(crate) fn app_with_oidc(\n    state: AppState,\n    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,\n) -> Router {\n    Router::new()\n        .route(\n            \"/mcp\",\n            post(mcp_post)\n                .get(mcp_get)\n                .delete(mcp_delete)\n                .options(mcp_options),\n        )\n        .with_state(McpHttpState {\n            app: state,\n            _oidc_verifier: oidc_verifier,\n        })\n}\n'''
s = replace_once(s, old_app, new_app, "mcp router state")
s = replace_once(
    s,
    "async fn mcp_post(State(state): State<AppState>, request: Request<Body>) -> Response {",
    "async fn mcp_post(State(state): State<McpHttpState>, request: Request<Body>) -> Response {",
    "mcp_post state",
)
s = s.replace("tool_call_result(&state, object).await", "tool_call_result(&state.app, object).await")
mcp.write_text(s, encoding="utf-8")

# Wire one verifier instance into both the existing OIDC API and remote MCP.
runtime = Path("src/runtime.rs")
s = runtime.read_text(encoding="utf-8")
s = replace_once(
    s,
    "    let oidc_state = oidc::OidcState::from_env(config.db_path).await?;\n",
    "    let oidc_state = oidc::OidcState::from_env(config.db_path).await?;\n    let oidc_verifier = oidc_state.as_ref().map(oidc::OidcState::verifier);\n",
    "runtime verifier extraction",
)
s = replace_once(
    s,
    "        .merge(mcp::app(state))\n",
    "        .merge(mcp::app_with_oidc(state, oidc_verifier))\n",
    "runtime MCP OIDC wiring",
)
runtime.write_text(s, encoding="utf-8")
