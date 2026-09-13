from pathlib import Path

p = Path("src/oidc.rs")
text = p.read_text(encoding="utf-8")
old = '''    #[test]\n    fn oidc_rejects_non_https_configuration_and_malformed_tokens() {\n        assert!(validate_https_url("http://issuer.example").is_err());\n        let jwks: JwkSet = serde_json::from_value(json!({\n            "keys": [{"kty":"RSA","kid":"test-key","n":"s_FPnz9vWyE_0UcxDtM0cAXkxh3YA8Yg-78qj9L_pz3czvxbU2CDAVvi8a5X1Vm81nJmaSF3BMmhw1cLWyJGRdY3-QpmKo50PzSrXm0VVgf6h9orFXkJIR1P80zq23eu_Q2w0JarOQquqHdeSzqp6sQ6BmiszytZ9a4oVD36yUkeLukjTM3FRidn0t7GqD_qO_ov0guhNiIYPruuoU2oiZTs_5P_ocayMbg39Gc2ZsY1VEZFiLsR1vaE3406IjRyeOOpC0XrPeYH0IYRDwM-rjQIzPO3e7d8bSjZFyGrHXWVplVpjcWLGvCyHs6YUqtKr6kEYpqI1D6j5ESg_qOI1w","e":"AQAB"}]\n        })).unwrap();\n        let verifier =\n            OidcVerifier::from_jwks("https://issuer.example".into(), "blackboard".into(), jwks)\n                .unwrap();\n        assert!(verifier.verify("not-a-jwt").is_err());\n    }\n}'''
new = r'''    fn jwt_test_fixture() -> (OidcVerifier, jsonwebtoken::EncodingKey) {
        use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
        use rsa::{pkcs8::EncodePrivateKey, traits::PublicKeyParts, RsaPrivateKey};

        let mut rng = rand::rngs::OsRng;
        let private = RsaPrivateKey::new(&mut rng, 2048).unwrap();
        let n = URL_SAFE_NO_PAD.encode(private.n().to_bytes_be());
        let e = URL_SAFE_NO_PAD.encode(private.e().to_bytes_be());
        let jwks: JwkSet = serde_json::from_value(json!({
            "keys": [{
                "kty": "RSA", "kid": "jwt-test-key", "use": "sig", "alg": "RS256",
                "n": n, "e": e
            }]
        }))
        .unwrap();
        let verifier =
            OidcVerifier::from_jwks("https://issuer.example".into(), "blackboard".into(), jwks)
                .unwrap();
        let pem = private.to_pkcs8_pem(rsa::pkcs8::LineEnding::LF).unwrap();
        let encoding = jsonwebtoken::EncodingKey::from_rsa_pem(pem.as_bytes()).unwrap();
        (verifier, encoding)
    }

    fn signed_test_token(
        key: &jsonwebtoken::EncodingKey,
        issuer: &str,
        audience: &str,
        exp: u64,
    ) -> String {
        let mut header = jsonwebtoken::Header::new(Algorithm::RS256);
        header.kid = Some("jwt-test-key".to_owned());
        jsonwebtoken::encode(
            &header,
            &json!({
                "iss": issuer,
                "aud": audience,
                "sub": "remote-agent-1",
                "exp": exp
            }),
            key,
        )
        .unwrap()
    }

    fn now_epoch_seconds() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs()
    }

    #[test]
    fn oidc_accepts_valid_short_lived_token_and_normalizes_principal() {
        let (verifier, key) = jwt_test_fixture();
        let token = signed_test_token(
            &key,
            "https://issuer.example",
            "blackboard",
            now_epoch_seconds() + 3600,
        );
        assert_eq!(
            verifier.verify(&token).unwrap(),
            execution::Principal {
                provider: "oidc:https://issuer.example".to_owned(),
                subject: "remote-agent-1".to_owned(),
            }
        );
    }

    #[test]
    fn oidc_rejects_invalid_issuer_audience_expiry_and_signature() {
        let (verifier, key) = jwt_test_fixture();
        let now = now_epoch_seconds();
        assert!(verifier
            .verify(&signed_test_token(
                &key,
                "https://wrong-issuer.example",
                "blackboard",
                now + 3600,
            ))
            .is_err());
        assert!(verifier
            .verify(&signed_test_token(
                &key,
                "https://issuer.example",
                "wrong-audience",
                now + 3600,
            ))
            .is_err());
        assert!(verifier
            .verify(&signed_test_token(
                &key,
                "https://issuer.example",
                "blackboard",
                now - 3600,
            ))
            .is_err());

        let token = signed_test_token(
            &key,
            "https://issuer.example",
            "blackboard",
            now + 3600,
        );
        let mut parts = token.split('.').map(str::to_owned).collect::<Vec<_>>();
        let first = parts[2].as_bytes()[0];
        parts[2].replace_range(0..1, if first == b'A' { "B" } else { "A" });
        assert!(verifier.verify(&parts.join(".")).is_err());
    }

    #[test]
    fn oidc_rejects_non_https_configuration_and_malformed_tokens() {
        assert!(validate_https_url("http://issuer.example").is_err());
        let (verifier, _) = jwt_test_fixture();
        assert!(verifier.verify("not-a-jwt").is_err());
    }
}'''
if old not in text:
    raise SystemExit("OIDC test replacement target not found")
p.write_text(text.replace(old, new, 1), encoding="utf-8")

cargo = Path("Cargo.toml")
text = cargo.read_text(encoding="utf-8")
needle = '[dev-dependencies]\n'
if 'rsa = ' not in text:
    if needle not in text:
        raise SystemExit("dev-dependencies section not found")
    text = text.replace(needle, needle + 'rsa = { version = "0.9", features = ["pem"] }\n', 1)
cargo.write_text(text, encoding="utf-8")
