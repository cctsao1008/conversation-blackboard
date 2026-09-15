from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

p = Path("src/mcp_contract_tests.rs")
s = p.read_text(encoding="utf-8")

old = r'''fn bearer_verifier_and_token(subject: &str) -> (oidc::OidcVerifier, String) {
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
    let verifier =
        oidc::OidcVerifier::from_jwks("https://issuer.example".into(), "blackboard".into(), jwks)
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
'''
new = r'''fn bearer_verifier_and_token(subject: &str) -> (oidc::OidcVerifier, String) {
    bearer_verifier_and_token_with_claims(
        subject,
        "https://issuer.example",
        "blackboard",
        300,
    )
}

fn bearer_verifier_and_token_with_claims(
    subject: &str,
    token_issuer: &str,
    token_audience: &str,
    expiry_offset_seconds: i64,
) -> (oidc::OidcVerifier, String) {
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
    let verifier =
        oidc::OidcVerifier::from_jwks("https://issuer.example".into(), "blackboard".into(), jwks)
            .unwrap();
    let pem = private.to_pkcs8_pem(rsa::pkcs8::LineEnding::LF).unwrap();
    let encoding = jsonwebtoken::EncodingKey::from_rsa_pem(pem.as_bytes()).unwrap();
    let mut header = jsonwebtoken::Header::new(Algorithm::RS256);
    header.kid = Some("mcp-test-key".to_owned());
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let exp = (now + expiry_offset_seconds).max(0) as u64;
    let token = jsonwebtoken::encode(
        &header,
        &json!({
            "iss": token_issuer,
            "aud": token_audience,
            "sub": subject,
            "exp": exp
        }),
        &encoding,
    )
    .unwrap();
    (verifier, token)
}
'''
s = replace_once(s, old, new, "configurable bearer claims helper")

anchor = r'''#[tokio::test]
async fn mcp_bearer_write_uses_oidc_principal_and_matching_blackboard_grant() {'''
tests = r'''async fn assert_rejected_bearer(verifier: oidc::OidcVerifier, token: String, id: i64) {
    let fixture = fixture();
    let router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );
    let response = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": {
                "name": "blackboard_write",
                "arguments": {
                    "participant_id": "single-main",
                    "channel": "control-systems",
                    "body": "must-not-land",
                    "nonce": format!("issue100-invalid-claims-{id}")
                }
            }
        }),
        &format!("Bearer {token}"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(
        response.headers().get(header::WWW_AUTHENTICATE).unwrap(),
        "Bearer"
    );
}

#[tokio::test]
async fn expired_bearer_is_rejected_at_mcp_transport_gate() {
    let (verifier, token) = bearer_verifier_and_token_with_claims(
        "remote-agent-1",
        "https://issuer.example",
        "blackboard",
        -3600,
    );
    assert_rejected_bearer(verifier, token, 105).await;
}

#[tokio::test]
async fn wrong_issuer_bearer_is_rejected_at_mcp_transport_gate() {
    let (verifier, token) = bearer_verifier_and_token_with_claims(
        "remote-agent-1",
        "https://other-issuer.example",
        "blackboard",
        300,
    );
    assert_rejected_bearer(verifier, token, 106).await;
}

#[tokio::test]
async fn wrong_audience_bearer_is_rejected_at_mcp_transport_gate() {
    let (verifier, token) = bearer_verifier_and_token_with_claims(
        "remote-agent-1",
        "https://issuer.example",
        "other-resource",
        300,
    );
    assert_rejected_bearer(verifier, token, 107).await;
}

''' + anchor
s = replace_once(s, anchor, tests, "bearer claim regressions")
p.write_text(s, encoding="utf-8")
