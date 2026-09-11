use std::{
    collections::BTreeMap,
    sync::OnceLock,
    time::{SystemTime, UNIX_EPOCH},
};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};
use ring::hmac;
use serde_json::{json, Value};

use crate::signed_auth;

#[cfg(test)]
pub const CREDENTIAL_BUNDLE_PREFIX: &str = "bbcred-v1:";
pub const BROWSER_CHALLENGE_PURPOSE: &str = "browser-connect-v1";
pub const HTTP_REQUEST_PURPOSE: &str = "http-request-auth-v1";
const CHALLENGE_TOKEN_PURPOSE: &str = "browser-challenge-token-v1";
const CHALLENGE_TTL_SECS: u64 = 60;
const CHALLENGE_FUTURE_SKEW_SECS: u64 = 5;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BrowserChallenge {
    pub challenge: String,
    pub expires_at: u64,
    pub challenge_token: String,
}

pub fn issue_browser_challenge(participant_id: &str) -> BrowserChallenge {
    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    let challenge = URL_SAFE_NO_PAD.encode(bytes);
    let expires_at = unix_time().saturating_add(CHALLENGE_TTL_SECS);
    let token_input = canonical_challenge_token_bytes(participant_id, &challenge, expires_at);
    let challenge_token = URL_SAFE_NO_PAD.encode(hmac::sign(server_hmac_key(), &token_input));
    BrowserChallenge {
        challenge,
        expires_at,
        challenge_token,
    }
}

pub fn verify_browser_challenge_token(
    participant_id: &str,
    challenge: &str,
    expires_at: u64,
    challenge_token: &str,
) -> bool {
    let now = unix_time();
    if expires_at < now
        || expires_at > now.saturating_add(CHALLENGE_TTL_SECS + CHALLENGE_FUTURE_SKEW_SECS)
    {
        return false;
    }
    let Ok(tag) = URL_SAFE_NO_PAD.decode(challenge_token) else {
        return false;
    };
    let token_input = canonical_challenge_token_bytes(participant_id, challenge, expires_at);
    hmac::verify(server_hmac_key(), &token_input, &tag).is_ok()
}

pub fn canonical_browser_challenge_bytes(
    participant_id: &str,
    challenge: &str,
    expires_at: u64,
) -> Vec<u8> {
    canonical_json([
        ("challenge", json!(challenge)),
        ("expires_at", json!(expires_at)),
        ("participant_id", json!(participant_id)),
        ("purpose", json!(BROWSER_CHALLENGE_PURPOSE)),
        ("signature_version", json!(signed_auth::SIGNATURE_SCHEME)),
    ])
}

pub fn verify_browser_challenge_signature(
    public_key: &str,
    signature: &str,
    participant_id: &str,
    challenge: &str,
    expires_at: u64,
) -> bool {
    let payload = canonical_browser_challenge_bytes(participant_id, challenge, expires_at);
    signed_auth::verify_message_signature(public_key, signature, &payload)
}

pub fn canonical_http_request_bytes(
    participant_id: &str,
    method: &str,
    request_target: &str,
) -> Vec<u8> {
    canonical_json([
        ("method", json!(method)),
        ("participant_id", json!(participant_id)),
        ("purpose", json!(HTTP_REQUEST_PURPOSE)),
        ("request_target", json!(request_target)),
        ("signature_version", json!(signed_auth::SIGNATURE_SCHEME)),
    ])
}

pub fn verify_http_request_signature(
    public_key: &str,
    signature: &str,
    participant_id: &str,
    method: &str,
    request_target: &str,
) -> bool {
    let payload = canonical_http_request_bytes(participant_id, method, request_target);
    signed_auth::verify_message_signature(public_key, signature, &payload)
}

#[cfg(test)]
pub fn credential_bundle(participant_id: &str, private_key: &str) -> String {
    let bytes = serde_json::to_vec(&json!({
        "participant_id": participant_id,
        "private_key": private_key,
        "version": "bbcred-v1"
    }))
    .expect("credential bundle must serialize");
    format!(
        "{CREDENTIAL_BUNDLE_PREFIX}{}",
        URL_SAFE_NO_PAD.encode(bytes)
    )
}

fn canonical_challenge_token_bytes(
    participant_id: &str,
    challenge: &str,
    expires_at: u64,
) -> Vec<u8> {
    canonical_json([
        ("challenge", json!(challenge)),
        ("expires_at", json!(expires_at)),
        ("participant_id", json!(participant_id)),
        ("purpose", json!(CHALLENGE_TOKEN_PURPOSE)),
    ])
}

fn canonical_json<const N: usize>(pairs: [(&str, Value); N]) -> Vec<u8> {
    let mut payload = BTreeMap::<String, Value>::new();
    for (key, value) in pairs {
        payload.insert(key.to_owned(), value);
    }
    serde_json::to_vec(&payload).expect("canonical auth payload must serialize")
}

fn server_hmac_key() -> &'static hmac::Key {
    static KEY: OnceLock<hmac::Key> = OnceLock::new();
    KEY.get_or_init(|| {
        let mut bytes = [0_u8; 32];
        OsRng.fill_bytes(&mut bytes);
        hmac::Key::new(hmac::HMAC_SHA256, &bytes)
    })
}

fn unix_time() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn challenge_token_is_bound_to_participant_and_expiry() {
        let challenge = issue_browser_challenge("single-main");
        assert!(verify_browser_challenge_token(
            "single-main",
            &challenge.challenge,
            challenge.expires_at,
            &challenge.challenge_token,
        ));
        assert!(!verify_browser_challenge_token(
            "rotary-main",
            &challenge.challenge,
            challenge.expires_at,
            &challenge.challenge_token,
        ));
        assert!(!verify_browser_challenge_token(
            "single-main",
            "tampered",
            challenge.expires_at,
            &challenge.challenge_token,
        ));
    }

    #[test]
    fn browser_bundle_is_one_field_container() {
        let bundle = credential_bundle("single-main", "ed25519-sk:example");
        assert!(bundle.starts_with(CREDENTIAL_BUNDLE_PREFIX));
        assert!(!bundle.contains("single-main"));
    }
}
