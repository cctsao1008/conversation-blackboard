from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# Allow sibling contract tests to construct an in-memory verifier from a test JWKS.
oidc = Path("src/oidc.rs")
s = oidc.read_text(encoding="utf-8")
s = replace_once(s, "    fn from_jwks(\n", "    pub(crate) fn from_jwks(\n", "OidcVerifier::from_jwks visibility")
oidc.write_text(s, encoding="utf-8")

tests = Path("src/mcp_contract_tests.rs")
s = tests.read_text(encoding="utf-8")
s = replace_once(s, "use std::path::PathBuf;", "use std::{path::PathBuf, sync::Arc};", "Arc import")
s = replace_once(
    s,
    "use serde_json::{json, Value};\n",
    "use jsonwebtoken::{jwk::JwkSet, Algorithm};\nuse rusqlite::params;\nuse serde_json::{json, Value};\n",
    "JWT and SQL imports",
)
s = replace_once(
    s,
    "use crate::{authorization, contract_schema, db, http::AppState, identity, mcp, participant_auth};",
    "use crate::{authorization, contract_schema, db, http::AppState, identity, mcp, oidc, participant_auth};",
    "OIDC module import",
)

anchor = '''async fn response_json(response: Response) -> (StatusCode, Value) {\n'''
helpers = r'''fn bearer_verifier_and_token(subject: &str) -> (oidc::OidcVerifier, String) {
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
    use rsa::{pkcs8::EncodePrivateKey, traits::PublicKeyParts, RsaPrivateKey};

    let mut rng = rand::rngs::OsRng;
    let private = RsaPrivateKey::new(&mut rng, 2048).unwrap();
    let n = URL_SAFE_NO_PAD.encode(private.n().to_bytes_be());
    let e = URL_SAFE_NO_PAD.encode(private.e().to_bytes_be());
    let jwks: JwkSet = serde_json::from_value(json!({
        "keys": [{
            "kty": "RSA", "kid": "mcp-test-key", "use": "sig", "alg": "RS256",
            "n": n, "e": e
        }]
    }))
    .unwrap();
    let verifier = oidc::OidcVerifier::from_jwks(
        "https://issuer.example".into(),
        "blackboard".into(),
        jwks,
    )
    .unwrap();
    let pem = private.to_pkcs8_pem(rsa::pkcs8::LineEnding::LF).unwrap();
    let encoding = jsonwebtoken::EncodingKey::from_rsa_pem(pem.as_bytes()).unwrap();
    let mut header = jsonwebtoken::Header::new(Algorithm::RS256);
    header.kid = Some("mcp-test-key".to_owned());
    let exp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
        + 300;
    let token = jsonwebtoken::encode(
        &header,
        &json!({
            "iss": "https://issuer.example",
            "aud": "blackboard",
            "sub": subject,
            "exp": exp
        }),
        &encoding,
    )
    .unwrap();
    (verifier, token)
}

fn bearer_fixture(grant_resource: Option<&str>) -> (Fixture, String) {
    let mut fixture = fixture();
    let (verifier, token) = bearer_verifier_and_token("remote-agent-1");
    if let Some(resource) = grant_resource {
        let conn = db::connect(&fixture.db_path).unwrap();
        authorization::ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants (principal_provider, principal_subject, participant_id, capability, resource) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                "oidc:https://issuer.example",
                "remote-agent-1",
                "single-main",
                authorization::POST_MESSAGE,
                resource
            ],
        )
        .unwrap();
    }
    fixture.router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );
    (fixture, token)
}

async fn request_with_authorization(
    router: &Router,
    body: Value,
    authorization: &str,
) -> Response {
    let request = Request::builder()
        .method(Method::POST)
        .uri("/mcp")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, "application/json, text/event-stream")
        .header("mcp-protocol-version", "2025-11-25")
        .header(header::AUTHORIZATION, authorization)
        .body(Body::from(body.to_string()))
        .unwrap();
    router.clone().oneshot(request).await.unwrap()
}

''' + anchor
s = replace_once(s, anchor, helpers, "bearer test helpers")

# Insert focused bearer authority tests before the first existing tokio test.
test_anchor = '''#[tokio::test]\nasync fn stdio_dispatch_supports_lifecycle_discovery_and_notifications() {'''
new_tests = r'''#[tokio::test]
async fn mcp_bearer_write_uses_oidc_principal_and_matching_blackboard_grant() {
    let (fixture, token) = bearer_fixture(Some("control-systems"));
    let response = request_with_authorization(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {
                "name": "blackboard_write",
                "arguments": {
                    "participant_id": "single-main",
                    "channel": "control-systems",
                    "body": "bearer-authorized",
                    "nonce": "issue100-bearer-ok"
                }
            }
        }),
        &format!("Bearer {token}"),
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(value["result"]["isError"], false);
    assert_eq!(value["result"]["structuredContent"]["status"], "created");

    let conn = db::connect(&fixture.db_path).unwrap();
    let audit = crate::execution::get_execution_audit_bundle(
        &conn,
        "single-main",
        "issue100-bearer-ok",
    )
    .unwrap()
    .unwrap();
    assert_eq!(audit.authorization.principal.provider, "oidc:https://issuer.example");
    assert_eq!(audit.authorization.principal.subject, "remote-agent-1");
    assert_eq!(audit.authorization.mechanism, oidc::OIDC_MECHANISM);
}

#[tokio::test]
async fn mcp_bearer_write_denies_resource_mismatch() {
    let (fixture, token) = bearer_fixture(Some("control-systems"));
    let response = request_with_authorization(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {
                "name": "blackboard_write",
                "arguments": {
                    "participant_id": "single-main",
                    "channel": "blackboard-lounge",
                    "body": "must-not-land",
                    "nonce": "issue100-bearer-mismatch"
                }
            }
        }),
        &format!("Bearer {token}"),
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(tool_error_code(&value), "forbidden");
}

#[tokio::test]
async fn mcp_valid_bearer_without_blackboard_grant_is_forbidden() {
    let (fixture, token) = bearer_fixture(None);
    let response = request_with_authorization(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {
                "name": "blackboard_write",
                "arguments": {
                    "participant_id": "single-main",
                    "channel": "control-systems",
                    "body": "must-not-land",
                    "nonce": "issue100-bearer-no-grant"
                }
            }
        }),
        &format!("Bearer {token}"),
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(tool_error_code(&value), "forbidden");
}

#[tokio::test]
async fn invalid_bearer_never_falls_back_to_valid_participant_hmac() {
    let fixture = fixture();
    let arguments = write_arguments(
        &fixture.single_secret,
        "single-main",
        "control-systems",
        "message",
        "hmac-must-not-rescue-invalid-bearer",
        None,
        "issue100-no-fallback",
    );
    let response = request_with_authorization(
        &fixture.router,
        json!({
            "jsonrpc": "2.0",
            "id": 103,
            "method": "tools/call",
            "params": {"name": "blackboard_write", "arguments": arguments}
        }),
        "Bearer definitely-not-a-jwt",
    )
    .await;
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(
        response.headers().get(header::WWW_AUTHENTICATE).unwrap(),
        "Bearer"
    );
    let conn = db::connect(&fixture.db_path).unwrap();
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM messages WHERE body = ?1",
            ["hmac-must-not-rescue-invalid-bearer"],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(count, 0);
}

''' + test_anchor
s = replace_once(s, test_anchor, new_tests, "bearer regression tests")
tests.write_text(s, encoding="utf-8")
