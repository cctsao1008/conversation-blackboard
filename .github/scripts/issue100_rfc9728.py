from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# runtime.rs: add canonical external MCP resource URL configuration.
p = Path("src/runtime.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "    pub registration_key: Option<String>,\n",
    "    pub registration_key: Option<String>,\n    pub mcp_resource_url: Option<String>,\n",
    "RuntimeConfig resource field",
)
s = replace_once(
    s,
    "        let registration_key = env::var(\"BLACKBOARD_REGISTRATION_KEY\")\n            .ok()\n            .filter(|value| !value.is_empty());\n\n        Self {\n            host,\n            port,\n            db_path,\n            registration_key,\n        }\n",
    "        let registration_key = env::var(\"BLACKBOARD_REGISTRATION_KEY\")\n            .ok()\n            .filter(|value| !value.is_empty());\n        let mcp_resource_url = env::var(\"BLACKBOARD_MCP_RESOURCE_URL\")\n            .ok()\n            .map(|value| value.trim().to_owned())\n            .filter(|value| !value.is_empty());\n\n        Self {\n            host,\n            port,\n            db_path,\n            registration_key,\n            mcp_resource_url,\n        }\n",
    "RuntimeConfig env parsing",
)
s = replace_once(
    s,
    "        .merge(mcp::app_with_oidc(state, oidc_verifier))\n",
    "        .merge(mcp::app_with_remote_auth(\n            state,\n            oidc_verifier,\n            config.mcp_resource_url.as_deref(),\n        )?)\n",
    "runtime MCP app composition",
)
p.write_text(s, encoding="utf-8")

# main.rs: satisfy RuntimeConfig construction expectations by preserving field use only through builder.
# No direct struct literal exists here; no patch needed.

# oidc.rs: expose issuer identifier for protected-resource metadata.
p = Path("src/oidc.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "impl OidcVerifier {\n    pub(crate) fn from_jwks(\n",
    "impl OidcVerifier {\n    pub(crate) fn issuer(&self) -> &str {\n        &self.issuer\n    }\n\n    pub(crate) fn from_jwks(\n",
    "OidcVerifier issuer getter",
)
p.write_text(s, encoding="utf-8")

# mcp.rs: RFC9728 resource config, well-known route, and richer Bearer challenge.
p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "    routing::post,\n",
    "    routing::{get, post},\n",
    "axum get import",
)
s = replace_once(
    s,
    "struct McpHttpState {\n    app: AppState,\n    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,\n}\n",
    "struct McpHttpState {\n    app: AppState,\n    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,\n    protected_resource: Option<ProtectedResourceConfig>,\n}\n\n#[derive(Clone, Debug)]\nstruct ProtectedResourceConfig {\n    resource: String,\n    metadata_path: String,\n    metadata_url: String,\n}\n",
    "McpHttpState protected resource",
)
s = replace_once(
    s,
    "pub fn app(state: AppState) -> Router {\n    app_with_oidc(state, None)\n}\n\npub(crate) fn app_with_oidc(\n    state: AppState,\n    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,\n) -> Router {\n    Router::new()\n        .route(\n            \"/mcp\",\n            post(mcp_post)\n                .get(mcp_get)\n                .delete(mcp_delete)\n                .options(mcp_options),\n        )\n        .with_state(McpHttpState {\n            app: state,\n            oidc_verifier,\n        })\n}\n",
    "pub fn app(state: AppState) -> Router {\n    app_with_oidc(state, None)\n}\n\npub(crate) fn app_with_oidc(\n    state: AppState,\n    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,\n) -> Router {\n    app_with_remote_auth(state, oidc_verifier, None).expect(\"no MCP resource URL is always valid\")\n}\n\npub(crate) fn app_with_remote_auth(\n    state: AppState,\n    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,\n    resource_url: Option<&str>,\n) -> Result<Router, Box<dyn std::error::Error + Send + Sync>> {\n    let protected_resource = match resource_url {\n        Some(resource_url) => Some(ProtectedResourceConfig::parse(resource_url)?),\n        None => None,\n    };\n    if protected_resource.is_some() && oidc_verifier.is_none() {\n        return Err(\"BLACKBOARD_MCP_RESOURCE_URL requires OIDC configuration\".into());\n    }\n    let state = McpHttpState {\n        app: state,\n        oidc_verifier,\n        protected_resource: protected_resource.clone(),\n    };\n    let mut router = Router::new().route(\n        \"/mcp\",\n        post(mcp_post)\n            .get(mcp_get)\n            .delete(mcp_delete)\n            .options(mcp_options),\n    );\n    if let Some(config) = protected_resource {\n        router = router.route(&config.metadata_path, get(protected_resource_metadata));\n    }\n    Ok(router.with_state(state))\n}\n\nimpl ProtectedResourceConfig {\n    fn parse(resource: &str) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {\n        let url = Url::parse(resource)?;\n        if url.scheme() != \"https\" || url.host_str().is_none() || url.fragment().is_some() {\n            return Err(\"BLACKBOARD_MCP_RESOURCE_URL must be an absolute HTTPS URL without a fragment\".into());\n        }\n        if url.query().is_some() {\n            return Err(\"BLACKBOARD_MCP_RESOURCE_URL must not contain a query\".into());\n        }\n        let path = url.path();\n        let suffix = if path == \"/\" {\n            String::new()\n        } else {\n            path.to_owned()\n        };\n        let metadata_path = format!(\"/.well-known/oauth-protected-resource{suffix}\");\n        let mut metadata = url.clone();\n        metadata.set_path(&metadata_path);\n        metadata.set_query(None);\n        metadata.set_fragment(None);\n        Ok(Self {\n            resource: url.to_string(),\n            metadata_path,\n            metadata_url: metadata.to_string(),\n        })\n    }\n}\n\nasync fn protected_resource_metadata(State(state): State<McpHttpState>) -> Response {\n    let Some(resource) = state.protected_resource.as_ref() else {\n        return StatusCode::NOT_FOUND.into_response();\n    };\n    let Some(verifier) = state.oidc_verifier.as_ref() else {\n        return StatusCode::NOT_FOUND.into_response();\n    };\n    (\n        [(header::CACHE_CONTROL, \"public, max-age=300\")],\n        Json(json!({\n            \"resource\": resource.resource,\n            \"authorization_servers\": [verifier.issuer()],\n            \"bearer_methods_supported\": [\"header\"]\n        })),\n    )\n        .into_response()\n}\n",
    "remote auth app and metadata route",
)
s = replace_once(
    s,
    "        Err(()) => return bearer_unauthorized(id),\n",
    "        Err(()) => return bearer_unauthorized(id, state.protected_resource.as_ref()),\n",
    "bearer unauthorized call",
)
# Find helper exactly and enrich it.
old = '''fn bearer_unauthorized(id: Value) -> Response {\n    let mut response = jsonrpc_http_error(StatusCode::UNAUTHORIZED, id, -32001, \"unauthorized\");\n    response\n        .headers_mut()\n        .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static(\"Bearer\"));\n    response\n}\n'''
new = '''fn bearer_unauthorized(id: Value, resource: Option<&ProtectedResourceConfig>) -> Response {\n    let mut response = jsonrpc_http_error(StatusCode::UNAUTHORIZED, id, -32001, \"unauthorized\");\n    let challenge = match resource {\n        Some(resource) => format!(\"Bearer resource_metadata=\\\"{}\\\"\", resource.metadata_url),\n        None => \"Bearer\".to_owned(),\n    };\n    if let Ok(value) = HeaderValue::from_str(&challenge) {\n        response.headers_mut().insert(header::WWW_AUTHENTICATE, value);\n    }\n    response\n}\n'''
s = replace_once(s, old, new, "Bearer challenge helper")
p.write_text(s, encoding="utf-8")

# Contract tests: resource derivation, metadata, 401 challenge, legacy fallback.
p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")
anchor = '''#[tokio::test]\nasync fn stdio_dispatch_supports_lifecycle_discovery_and_notifications() {'''
tests = r'''#[tokio::test]
async fn mcp_rfc9728_metadata_uses_canonical_resource_and_oidc_issuer() {
    let fixture = fixture();
    let (verifier, _) = bearer_verifier_and_token("remote-agent-1");
    let router = mcp::app_with_remote_auth(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
        Some("https://board.example/mcp"),
    )
    .unwrap();
    let response = router
        .oneshot(
            Request::builder()
                .method(Method::GET)
                .uri("/.well-known/oauth-protected-resource/mcp")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(value["resource"], "https://board.example/mcp");
    assert_eq!(value["authorization_servers"][0], "https://issuer.example");
    assert_eq!(value["bearer_methods_supported"][0], "header");
}

#[tokio::test]
async fn invalid_bearer_challenge_points_to_rfc9728_metadata_when_configured() {
    let fixture = fixture();
    let (verifier, _) = bearer_verifier_and_token("remote-agent-1");
    let router = mcp::app_with_remote_auth(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
        Some("https://board.example/mcp"),
    )
    .unwrap();
    let response = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 104,
            "method": "tools/call",
            "params": {"name": "blackboard_write", "arguments": {}}
        }),
        "Bearer invalid",
    )
    .await;
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(
        response.headers().get(header::WWW_AUTHENTICATE).unwrap(),
        "Bearer resource_metadata=\"https://board.example/.well-known/oauth-protected-resource/mcp\""
    );
}

#[test]
fn mcp_resource_url_requires_https_and_oidc() {
    let fixture = fixture();
    let state = AppState {
        db_path: fixture.db_path,
        registration_key: None,
    };
    assert!(mcp::app_with_remote_auth(state.clone(), None, Some("https://board.example/mcp")).is_err());
    let (verifier, _) = bearer_verifier_and_token("remote-agent-1");
    assert!(mcp::app_with_remote_auth(
        state,
        Some(Arc::new(verifier)),
        Some("http://board.example/mcp")
    )
    .is_err());
}

''' + anchor
s = replace_once(s, anchor, tests, "RFC9728 contract tests")
p.write_text(s, encoding="utf-8")
