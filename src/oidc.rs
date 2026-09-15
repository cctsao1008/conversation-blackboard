use std::{collections::HashMap, env, path::PathBuf, sync::Arc, time::Duration};

use axum::{
    body::{to_bytes, Body},
    extract::{Path, State},
    http::{header, HeaderMap, Request, StatusCode},
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use jsonwebtoken::{decode, decode_header, jwk::JwkSet, Algorithm, DecodingKey, Validation};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use url::Url;

use crate::{db, execution, identity};

const MAX_BODY_BYTES: usize = 64 * 1024;
pub(crate) const OIDC_MECHANISM: &str = "oidc-bearer-jwt";

#[derive(Clone)]
pub struct OidcState {
    db_path: PathBuf,
    verifier: Arc<OidcVerifier>,
}

#[derive(Clone)]
pub(crate) struct OidcVerifier {
    issuer: String,
    audience: String,
    keys: Arc<HashMap<String, DecodingKey>>,
}

#[derive(Debug, Deserialize)]
struct DiscoveryDocument {
    issuer: String,
    jwks_uri: String,
}

#[derive(Debug)]
struct ApiError {
    status: StatusCode,
    code: &'static str,
}

impl ApiError {
    fn unauthorized() -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            code: "unauthorized",
        }
    }

    fn forbidden() -> Self {
        Self {
            status: StatusCode::FORBIDDEN,
            code: "forbidden",
        }
    }

    fn bad_request(code: &'static str) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            code,
        }
    }

    fn unavailable() -> Self {
        Self {
            status: StatusCode::SERVICE_UNAVAILABLE,
            code: "database_unavailable",
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(json!({"error": self.code}))).into_response()
    }
}

impl OidcState {
    pub async fn from_env(
        db_path: PathBuf,
    ) -> Result<Option<Self>, Box<dyn std::error::Error + Send + Sync>> {
        let issuer = env::var("BLACKBOARD_OIDC_ISSUER")
            .ok()
            .filter(|v| !v.trim().is_empty());
        let audience = env::var("BLACKBOARD_OIDC_AUDIENCE")
            .ok()
            .filter(|v| !v.trim().is_empty());
        let (Some(issuer), Some(audience)) = (issuer, audience) else {
            if env::var("BLACKBOARD_OIDC_ISSUER").is_ok()
                || env::var("BLACKBOARD_OIDC_AUDIENCE").is_ok()
            {
                return Err("BLACKBOARD_OIDC_ISSUER and BLACKBOARD_OIDC_AUDIENCE must be configured together".into());
            }
            return Ok(None);
        };
        let issuer = issuer.trim_end_matches('/').to_owned();
        validate_https_url(&issuer)?;

        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .build()?;
        let jwks_url = match env::var("BLACKBOARD_OIDC_JWKS_URL")
            .ok()
            .filter(|v| !v.trim().is_empty())
        {
            Some(url) => {
                validate_https_url(&url)?;
                url
            }
            None => {
                let discovery_url = format!("{issuer}/.well-known/openid-configuration");
                let discovery = client
                    .get(discovery_url)
                    .send()
                    .await?
                    .error_for_status()?
                    .json::<DiscoveryDocument>()
                    .await?;
                if discovery.issuer.trim_end_matches('/') != issuer {
                    return Err("OIDC discovery issuer mismatch".into());
                }
                validate_https_url(&discovery.jwks_uri)?;
                discovery.jwks_uri
            }
        };
        let jwks = client
            .get(jwks_url)
            .send()
            .await?
            .error_for_status()?
            .json::<JwkSet>()
            .await?;
        let verifier = OidcVerifier::from_jwks(issuer, audience, jwks)?;
        Ok(Some(Self {
            db_path,
            verifier: Arc::new(verifier),
        }))
    }
}

impl OidcState {
    pub(crate) fn verifier(&self) -> Arc<OidcVerifier> {
        self.verifier.clone()
    }
}

impl OidcVerifier {
    pub(crate) fn from_jwks(
        issuer: String,
        audience: String,
        jwks: JwkSet,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let mut keys = HashMap::new();
        for jwk in &jwks.keys {
            let Some(kid) = jwk.common.key_id.as_deref() else {
                continue;
            };
            if let Ok(key) = DecodingKey::from_jwk(jwk) {
                keys.insert(kid.to_owned(), key);
            }
        }
        if keys.is_empty() {
            return Err("OIDC JWKS contains no usable keyed signing keys".into());
        }
        Ok(Self {
            issuer,
            audience,
            keys: Arc::new(keys),
        })
    }

    pub(crate) fn verify(&self, token: &str) -> Result<execution::Principal, ()> {
        let header = decode_header(token).map_err(|_| ())?;
        if header.alg != Algorithm::RS256 {
            return Err(());
        }
        let kid = header.kid.as_deref().ok_or(())?;
        let key = self.keys.get(kid).ok_or(())?;
        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_audience(&[self.audience.as_str()]);
        validation.set_issuer(&[self.issuer.as_str()]);
        validation.set_required_spec_claims(&["exp", "iss", "aud", "sub"]);
        let data = decode::<Value>(token, key, &validation).map_err(|_| ())?;
        let subject = data
            .claims
            .get("sub")
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
            .ok_or(())?;
        Ok(execution::Principal {
            provider: format!("oidc:{}", self.issuer),
            subject: subject.to_owned(),
        })
    }
}

fn validate_https_url(value: &str) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let url = Url::parse(value)?;
    if url.scheme() != "https" || url.host_str().is_none() {
        return Err("OIDC URLs must use https".into());
    }
    Ok(())
}

pub fn app(state: OidcState) -> Router {
    Router::new()
        .route("/api/oidc/messages/{participant_id}", post(post_message))
        .with_state(state)
}

async fn post_message(
    State(state): State<OidcState>,
    Path(participant_id): Path<String>,
    headers: HeaderMap,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    let participant_id = identity::validate_participant_id(&participant_id)
        .ok_or_else(|| ApiError::bad_request("invalid_participant_id"))?;
    let token = bearer_token(&headers).ok_or_else(ApiError::unauthorized)?;
    let principal = state
        .verifier
        .verify(token)
        .map_err(|_| ApiError::unauthorized())?;
    let body = read_json_object(request).await?;
    if !only_keys(&body, &["channel", "kind", "body", "reply_to", "intent_id"]) {
        return Err(ApiError::bad_request("invalid_arguments"));
    }
    let channel = body
        .get("channel")
        .and_then(Value::as_str)
        .filter(|v| valid_name(v))
        .ok_or_else(|| ApiError::bad_request("invalid_channel"))?
        .to_owned();
    let kind = match body.get("kind") {
        None => "message".to_owned(),
        Some(Value::String(v)) if valid_kind(v) => v.clone(),
        _ => return Err(ApiError::bad_request("invalid_kind")),
    };
    let message_body = body
        .get("body")
        .and_then(Value::as_str)
        .filter(|v| !v.trim().is_empty())
        .ok_or_else(|| ApiError::bad_request("invalid_body"))?
        .to_owned();
    if message_body.len() > MAX_BODY_BYTES {
        return Err(ApiError {
            status: StatusCode::PAYLOAD_TOO_LARGE,
            code: "request_too_large",
        });
    }
    let reply_to = match body.get("reply_to") {
        None | Some(Value::Null) => None,
        Some(Value::Number(v)) => v
            .as_i64()
            .filter(|id| *id > 0)
            .map(Some)
            .ok_or_else(|| ApiError::bad_request("invalid_reply_to"))?,
        _ => return Err(ApiError::bad_request("invalid_reply_to")),
    };
    let intent_id = body
        .get("intent_id")
        .and_then(Value::as_str)
        .ok_or_else(|| ApiError::bad_request("invalid_intent_id"))?;
    let intent_id = execution::normalize_intent_id(intent_id)
        .map_err(|_| ApiError::bad_request("invalid_intent_id"))?;

    let db_path = state.db_path.clone();
    let participant_for_db = participant_id.clone();
    let channel_for_db = channel.clone();
    let kind_for_db = kind.clone();
    let body_for_db = message_body.clone();
    let intent_for_db = intent_id.clone();
    let principal_for_db = principal.clone();
    let result = tokio::task::spawn_blocking(
        move || -> rusqlite::Result<Option<execution::MessageExecutionResult>> {
            let conn = db::connect(&db_path)?;
            let Some(writer) = identity::get_web_participant(&conn, &participant_for_db)? else {
                return Ok(None);
            };
            let request_hash = execution::message_request_hash(
                &channel_for_db,
                &kind_for_db,
                &body_for_db,
                None,
                reply_to,
            );
            let authority = execution::AuthorityContext {
                principal: principal_for_db.clone(),
                mechanism: OIDC_MECHANISM.to_owned(),
            };
            let intent = execution::IntentEnvelope {
                intent_id: intent_for_db.clone(),
                participant_id: participant_for_db.clone(),
                conversation_ref: None,
                capability: execution::POST_MESSAGE_CAPABILITY.to_owned(),
                resource: channel_for_db.clone(),
                request_hash,
            };
            let ingress = execution::IngressProvenance {
                delivery_id: format!(
                    "oidc:{}:{}:{}",
                    principal_for_db.subject, participant_for_db, intent_for_db
                ),
                intent_id: intent_for_db.clone(),
                transport: "oidc-http".to_owned(),
                external_ref: intent_for_db.clone(),
                principal: principal_for_db,
            };
            execution::execute_message_intent(
                &conn,
                &writer,
                execution::MessageExecutionRequest {
                    authority: &authority,
                    ingress: &ingress,
                    intent: &intent,
                    channel: &channel_for_db,
                    kind: &kind_for_db,
                    body: &body_for_db,
                    reply_to,
                },
            )
            .map(Some)
        },
    )
    .await
    .map_err(|_| ApiError::unavailable())?
    .map_err(|_| ApiError::unavailable())?
    .ok_or_else(ApiError::forbidden)?;

    let (status, message, idempotent) = match result {
        execution::MessageExecutionResult::Created(message) => {
            (StatusCode::CREATED, message, false)
        }
        execution::MessageExecutionResult::Existing(message) => (StatusCode::OK, message, true),
        execution::MessageExecutionResult::AuthorizationDenied => return Err(ApiError::forbidden()),
        execution::MessageExecutionResult::IntentConflict => {
            return Err(ApiError {
                status: StatusCode::CONFLICT,
                code: "intent_conflict",
            })
        }
        execution::MessageExecutionResult::ReplyTargetNotFound => {
            return Err(ApiError::bad_request("reply_target_not_found"))
        }
        execution::MessageExecutionResult::ChannelArchived => {
            return Err(ApiError {
                status: StatusCode::CONFLICT,
                code: "channel_archived",
            })
        }
    };
    Ok((
        status,
        Json(json!({"idempotent": idempotent, "message": message})),
    )
        .into_response())
}

fn bearer_token(headers: &HeaderMap) -> Option<&str> {
    let value = headers.get(header::AUTHORIZATION)?.to_str().ok()?;
    let (scheme, token) = value.split_once(' ')?;
    if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() {
        return None;
    }
    Some(token)
}

async fn read_json_object(request: Request<Body>) -> Result<Map<String, Value>, ApiError> {
    let bytes = to_bytes(request.into_body(), MAX_BODY_BYTES + 1)
        .await
        .map_err(|_| ApiError::bad_request("invalid_json"))?;
    if bytes.len() > MAX_BODY_BYTES {
        return Err(ApiError {
            status: StatusCode::PAYLOAD_TOO_LARGE,
            code: "request_too_large",
        });
    }
    serde_json::from_slice::<Value>(&bytes)
        .ok()
        .and_then(|v| v.as_object().cloned())
        .ok_or_else(|| ApiError::bad_request("invalid_json"))
}

fn only_keys(object: &Map<String, Value>, allowed: &[&str]) -> bool {
    object.keys().all(|key| allowed.contains(&key.as_str()))
}

fn valid_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b':'))
}

fn valid_kind(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 32
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{authorization, db, identity};
    use jsonwebtoken::jwk::JwkSet;
    use rusqlite::params;
    use tempfile::tempdir;

    #[test]
    fn oidc_principal_is_issuer_and_subject_and_uses_scoped_grants() {
        let jwks: JwkSet = serde_json::from_value(json!({
            "keys": [{
                "kty": "RSA", "kid": "test-key", "use": "sig", "alg": "RS256",
                "n": "s_FPnz9vWyE_0UcxDtM0cAXkxh3YA8Yg-78qj9L_pz3czvxbU2CDAVvi8a5X1Vm81nJmaSF3BMmhw1cLWyJGRdY3-QpmKo50PzSrXm0VVgf6h9orFXkJIR1P80zq23eu_Q2w0JarOQquqHdeSzqp6sQ6BmiszytZ9a4oVD36yUkeLukjTM3FRidn0t7GqD_qO_ov0guhNiIYPruuoU2oiZTs_5P_ocayMbg39Gc2ZsY1VEZFiLsR1vaE3406IjRyeOOpC0XrPeYH0IYRDwM-rjQIzPO3e7d8bSjZFyGrHXWVplVpjcWLGvCyHs6YUqtKr6kEYpqI1D6j5ESg_qOI1w",
                "e": "AQAB"
            }]
        })).unwrap();
        let verifier =
            OidcVerifier::from_jwks("https://issuer.example".into(), "blackboard".into(), jwks)
                .unwrap();
        assert_eq!(verifier.keys.len(), 1);

        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        identity::provision_web_participant_identity(&conn, "agent-main", "agent", Some("Agent"))
            .unwrap()
            .unwrap();
        authorization::ensure_grant_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO principal_grants (principal_provider, principal_subject, participant_id, capability, resource) VALUES (?1, ?2, ?3, ?4, ?5)",
            params!["oidc:https://issuer.example", "remote-agent-1", "agent-main", authorization::POST_MESSAGE, "control-systems"],
        ).unwrap();
        let principal = execution::Principal {
            provider: "oidc:https://issuer.example".into(),
            subject: "remote-agent-1".into(),
        };
        assert!(authorization::authorize(
            &conn,
            &principal,
            "agent-main",
            authorization::POST_MESSAGE,
            Some("control-systems")
        )
        .unwrap());
        assert!(!authorization::authorize(
            &conn,
            &principal,
            "agent-main",
            authorization::POST_MESSAGE,
            Some("blackboard-lounge")
        )
        .unwrap());
    }

    fn jwt_test_fixture() -> (OidcVerifier, jsonwebtoken::EncodingKey) {
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

        let token = signed_test_token(&key, "https://issuer.example", "blackboard", now + 3600);
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
}
