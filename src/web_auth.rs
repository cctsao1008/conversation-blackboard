use std::{
    collections::BTreeMap,
    sync::OnceLock,
    time::{SystemTime, UNIX_EPOCH},
};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};
use ring::hmac;
use serde_json::{json, Value};
use subtle::ConstantTimeEq;
use url::Url;

use crate::signed_auth;

pub const WEB_SESSION_HEADER: &str = "x-blackboard-web-session";
pub const TOTP_PERIOD_SECS: u64 = 30;
pub const TOTP_DIGITS: u32 = 6;
pub const TOTP_MAX_FAILURES: i64 = 5;
pub const TOTP_LOCK_SECS: u64 = 60;
const WEB_SESSION_PREFIX: &str = "bbsess-v1";
const WEB_SESSION_PURPOSE: &str = "human-web-session-v1";
const WEB_SESSION_TTL_SECS: u64 = 8 * 60 * 60;
const BASE32_ALPHABET: &[u8; 32] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WebSession {
    pub participant_id: String,
    pub expires_at: u64,
    pub token: String,
}

pub fn current_unix_time() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub fn generate_totp_secret() -> String {
    let mut bytes = [0_u8; 20];
    OsRng.fill_bytes(&mut bytes);
    base32_encode(&bytes)
}

pub fn validate_totp_secret(secret: &str) -> bool {
    base32_decode(secret)
        .map(|bytes| (10..=64).contains(&bytes.len()))
        .unwrap_or(false)
}

pub fn otpauth_uri(participant_id: &str, secret: &str, issuer: &str) -> Option<String> {
    if participant_id.is_empty() || issuer.trim().is_empty() || !validate_totp_secret(secret) {
        return None;
    }
    let mut url = Url::parse("otpauth://totp/placeholder").ok()?;
    url.set_path(&format!("/{}:{}", issuer.trim(), participant_id));
    url.query_pairs_mut()
        .append_pair("secret", secret)
        .append_pair("issuer", issuer.trim())
        .append_pair("algorithm", "SHA1")
        .append_pair("digits", "6")
        .append_pair("period", "30");
    Some(url.into())
}

pub fn matching_totp_step(secret: &str, code: &str, now: u64) -> Option<u64> {
    if code.len() != TOTP_DIGITS as usize || !code.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let secret = base32_decode(secret)?;
    let current = now / TOTP_PERIOD_SECS;
    let mut candidates = [None; 3];
    candidates[0] = Some(current);
    candidates[1] = current.checked_sub(1);
    candidates[2] = current.checked_add(1);
    for step in candidates.into_iter().flatten() {
        let expected = totp_code_bytes(&secret, step, TOTP_DIGITS)?;
        if bool::from(expected.as_bytes().ct_eq(code.as_bytes())) {
            return Some(step);
        }
    }
    None
}

pub fn issue_web_session(participant_id: &str) -> WebSession {
    issue_web_session_at(participant_id, current_unix_time())
}

pub fn verify_web_session(token: &str) -> Option<WebSession> {
    verify_web_session_at(token, current_unix_time())
}

pub fn canonical_http_request_bytes(
    participant_id: &str,
    method: &str,
    request_target: &str,
) -> Vec<u8> {
    canonical_json([
        ("method", json!(method)),
        ("participant_id", json!(participant_id)),
        ("purpose", json!("http-request-auth-v1")),
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

fn issue_web_session_at(participant_id: &str, now: u64) -> WebSession {
    let expires_at = now.saturating_add(WEB_SESSION_TTL_SECS);
    let payload = canonical_json([
        ("expires_at", json!(expires_at)),
        ("participant_id", json!(participant_id)),
        ("purpose", json!(WEB_SESSION_PURPOSE)),
        ("version", json!(WEB_SESSION_PREFIX)),
    ]);
    let payload_encoded = URL_SAFE_NO_PAD.encode(&payload);
    let tag = hmac::sign(session_hmac_key(), &payload);
    let token = format!(
        "{WEB_SESSION_PREFIX}.{payload_encoded}.{}",
        URL_SAFE_NO_PAD.encode(tag.as_ref())
    );
    WebSession {
        participant_id: participant_id.to_owned(),
        expires_at,
        token,
    }
}

fn verify_web_session_at(token: &str, now: u64) -> Option<WebSession> {
    let mut parts = token.split('.');
    if parts.next()? != WEB_SESSION_PREFIX {
        return None;
    }
    let payload_encoded = parts.next()?;
    let tag_encoded = parts.next()?;
    if parts.next().is_some() {
        return None;
    }
    let payload = URL_SAFE_NO_PAD.decode(payload_encoded).ok()?;
    let tag = URL_SAFE_NO_PAD.decode(tag_encoded).ok()?;
    hmac::verify(session_hmac_key(), &payload, &tag).ok()?;
    let value: Value = serde_json::from_slice(&payload).ok()?;
    if value.get("version")?.as_str()? != WEB_SESSION_PREFIX
        || value.get("purpose")?.as_str()? != WEB_SESSION_PURPOSE
    {
        return None;
    }
    let participant_id = value.get("participant_id")?.as_str()?.to_owned();
    let expires_at = value.get("expires_at")?.as_u64()?;
    if participant_id.is_empty() || participant_id.len() > 64 || expires_at < now {
        return None;
    }
    Some(WebSession {
        participant_id,
        expires_at,
        token: token.to_owned(),
    })
}

fn session_hmac_key() -> &'static hmac::Key {
    static KEY: OnceLock<hmac::Key> = OnceLock::new();
    KEY.get_or_init(|| {
        let mut bytes = [0_u8; 32];
        OsRng.fill_bytes(&mut bytes);
        hmac::Key::new(hmac::HMAC_SHA256, &bytes)
    })
}

fn totp_code_bytes(secret: &[u8], step: u64, digits: u32) -> Option<String> {
    if !(6..=8).contains(&digits) {
        return None;
    }
    let key = hmac::Key::new(hmac::HMAC_SHA1_FOR_LEGACY_USE_ONLY, secret);
    let tag = hmac::sign(&key, &step.to_be_bytes());
    let digest = tag.as_ref();
    let offset = (digest.last()? & 0x0f) as usize;
    if offset + 4 > digest.len() {
        return None;
    }
    let binary = (u32::from(digest[offset] & 0x7f) << 24)
        | (u32::from(digest[offset + 1]) << 16)
        | (u32::from(digest[offset + 2]) << 8)
        | u32::from(digest[offset + 3]);
    let modulo = 10_u32.pow(digits);
    Some(format!(
        "{:0width$}",
        binary % modulo,
        width = digits as usize
    ))
}

fn base32_encode(bytes: &[u8]) -> String {
    let mut out = String::new();
    let mut buffer = 0_u32;
    let mut bits = 0_u8;
    for byte in bytes {
        buffer = (buffer << 8) | u32::from(*byte);
        bits += 8;
        while bits >= 5 {
            bits -= 5;
            let index = ((buffer >> bits) & 0x1f) as usize;
            out.push(BASE32_ALPHABET[index] as char);
        }
    }
    if bits > 0 {
        let index = ((buffer << (5 - bits)) & 0x1f) as usize;
        out.push(BASE32_ALPHABET[index] as char);
    }
    out
}

fn base32_decode(value: &str) -> Option<Vec<u8>> {
    if value.is_empty() || value.bytes().any(|byte| byte == b'=') {
        return None;
    }
    let mut out = Vec::new();
    let mut buffer = 0_u32;
    let mut bits = 0_u8;
    for byte in value.bytes() {
        let upper = byte.to_ascii_uppercase();
        let index = match upper {
            b'A'..=b'Z' => upper - b'A',
            b'2'..=b'7' => upper - b'2' + 26,
            _ => return None,
        };
        buffer = (buffer << 5) | u32::from(index);
        bits += 5;
        if bits >= 8 {
            bits -= 8;
            out.push(((buffer >> bits) & 0xff) as u8);
        }
    }
    Some(out)
}

fn canonical_json<const N: usize>(pairs: [(&str, Value); N]) -> Vec<u8> {
    let mut payload = BTreeMap::<String, Value>::new();
    for (key, value) in pairs {
        payload.insert(key.to_owned(), value);
    }
    serde_json::to_vec(&payload).expect("canonical auth payload must serialize")
}

#[cfg(test)]
pub fn totp_code_at(secret: &str, unix_time: u64, digits: u32) -> Option<String> {
    let secret = base32_decode(secret)?;
    totp_code_bytes(&secret, unix_time / TOTP_PERIOD_SECS, digits)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base32_round_trip_and_rfc6238_sha1_vector() {
        let raw = b"12345678901234567890";
        let secret = base32_encode(raw);
        assert_eq!(base32_decode(&secret).unwrap(), raw);
        assert_eq!(totp_code_at(&secret, 59, 8).as_deref(), Some("94287082"));
    }

    #[test]
    fn matching_totp_accepts_small_clock_skew() {
        let secret = generate_totp_secret();
        let now = 1_700_000_000;
        let code = totp_code_at(&secret, now, TOTP_DIGITS).unwrap();
        assert_eq!(
            matching_totp_step(&secret, &code, now),
            Some(now / TOTP_PERIOD_SECS)
        );
        assert!(matching_totp_step(&secret, "00000x", now).is_none());
    }

    #[test]
    fn web_session_is_authenticated_and_expires() {
        let now = 1_700_000_000;
        let session = issue_web_session_at("single-main", now);
        let verified = verify_web_session_at(&session.token, now + 10).unwrap();
        assert_eq!(verified.participant_id, "single-main");
        assert!(verify_web_session_at(&session.token, session.expires_at + 1).is_none());
        let mut tampered = session.token;
        tampered.push('x');
        assert!(verify_web_session_at(&tampered, now).is_none());
    }
}
