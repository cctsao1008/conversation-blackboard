use std::collections::BTreeMap;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};
use ring::hmac;
use serde_json::{json, Value};

pub const SIGNATURE_SCHEME: &str = "hmac-sha256-v1";
pub const SECRET_PREFIX: &str = "hmac-sha256-secret:";

/// Generate a fresh 256-bit participant shared secret.
///
/// The tuple shape is retained only as an internal call-site convenience while the
/// authentication model is HMAC-only: both values are the same shared secret.
/// There is no asymmetric keypair and no public key in this scheme.
pub fn generate_secret() -> (String, String) {
    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    let secret = format!("{SECRET_PREFIX}{}", URL_SAFE_NO_PAD.encode(bytes));
    (secret.clone(), secret)
}

pub fn validate_secret(secret: &str) -> bool {
    decode_secret(secret).is_some()
}

pub fn canonical_write_bytes(
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> Vec<u8> {
    let mut payload = BTreeMap::<String, Value>::new();
    payload.insert("auth_version".into(), json!(SIGNATURE_SCHEME));
    payload.insert("body".into(), json!(body));
    payload.insert("channel".into(), json!(channel));
    payload.insert("kind".into(), json!(kind));
    payload.insert("nonce".into(), json!(nonce));
    payload.insert("participant_id".into(), json!(participant_id));
    payload.insert("reply_to".into(), json!(reply_to));
    serde_json::to_vec(&payload).expect("canonical authenticated-write payload must serialize")
}

pub fn canonical_read_bytes(
    participant_id: &str,
    channel: &str,
    after: i64,
    limit: usize,
) -> Vec<u8> {
    let mut payload = BTreeMap::<String, Value>::new();
    payload.insert("after".into(), json!(after));
    payload.insert("auth_version".into(), json!(SIGNATURE_SCHEME));
    payload.insert("channel".into(), json!(channel));
    payload.insert("limit".into(), json!(limit));
    payload.insert("participant_id".into(), json!(participant_id));
    payload.insert("purpose".into(), json!("blackboard-read-v1"));
    serde_json::to_vec(&payload).expect("canonical authenticated-read payload must serialize")
}

#[cfg(test)]
#[allow(clippy::too_many_arguments)]
pub fn compute_write_proof(
    secret: &str,
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> Option<String> {
    let canonical = canonical_write_bytes(participant_id, channel, kind, body, reply_to, nonce);
    compute_message_proof(secret, &canonical)
}

#[cfg(test)]
pub fn compute_read_proof(
    secret: &str,
    participant_id: &str,
    channel: &str,
    after: i64,
    limit: usize,
) -> Option<String> {
    let canonical = canonical_read_bytes(participant_id, channel, after, limit);
    compute_message_proof(secret, &canonical)
}

#[cfg(test)]
pub fn compute_message_proof(secret: &str, message: &[u8]) -> Option<String> {
    let secret = decode_secret(secret)?;
    let key = hmac::Key::new(hmac::HMAC_SHA256, &secret);
    Some(URL_SAFE_NO_PAD.encode(hmac::sign(&key, message).as_ref()))
}

pub fn verify_message_proof(secret: &str, proof: &str, message: &[u8]) -> bool {
    let Some(secret) = decode_secret(secret) else {
        return false;
    };
    let Ok(proof) = URL_SAFE_NO_PAD.decode(proof) else {
        return false;
    };
    if proof.len() != 32 {
        return false;
    }
    let key = hmac::Key::new(hmac::HMAC_SHA256, &secret);
    hmac::verify(&key, message, &proof).is_ok()
}

#[allow(clippy::too_many_arguments)]
pub fn verify_write_proof(
    secret: &str,
    proof: &str,
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> bool {
    let canonical = canonical_write_bytes(participant_id, channel, kind, body, reply_to, nonce);
    verify_message_proof(secret, proof, &canonical)
}

pub fn verify_read_proof(
    secret: &str,
    proof: &str,
    participant_id: &str,
    channel: &str,
    after: i64,
    limit: usize,
) -> bool {
    let canonical = canonical_read_bytes(participant_id, channel, after, limit);
    verify_message_proof(secret, proof, &canonical)
}

fn decode_secret(secret: &str) -> Option<Vec<u8>> {
    let encoded = secret.strip_prefix(SECRET_PREFIX)?;
    if encoded.is_empty() {
        return None;
    }
    let decoded = URL_SAFE_NO_PAD.decode(encoded).ok()?;
    (decoded.len() == 32).then_some(decoded)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_secret_authenticates_canonical_write() {
        let (secret, registered) = generate_secret();
        assert_eq!(secret, registered);
        assert!(secret.starts_with(SECRET_PREFIX));
        assert!(validate_secret(&registered));

        let proof = compute_write_proof(
            &secret,
            "maker-main",
            "blackboard-lounge",
            "banter",
            "hello",
            None,
            "maker-001",
        )
        .unwrap();
        assert!(verify_write_proof(
            &registered,
            &proof,
            "maker-main",
            "blackboard-lounge",
            "banter",
            "hello",
            None,
            "maker-001",
        ));
    }

    #[test]
    fn proof_binds_every_persisted_write_field() {
        let (secret, registered) = generate_secret();
        let proof = compute_write_proof(
            &secret,
            "single-main",
            "control-systems",
            "insight",
            "payload",
            Some(7),
            "single-001",
        )
        .unwrap();

        assert!(!verify_write_proof(
            &registered,
            &proof,
            "rotary-main",
            "control-systems",
            "insight",
            "payload",
            Some(7),
            "single-001",
        ));
        assert!(!verify_write_proof(
            &registered,
            &proof,
            "single-main",
            "control-systems",
            "insight",
            "tampered",
            Some(7),
            "single-001",
        ));
        assert!(!verify_write_proof(
            &registered,
            &proof,
            "single-main",
            "control-systems",
            "insight",
            "payload",
            Some(7),
            "single-002",
        ));
    }

    #[test]
    fn wrong_secret_and_malformed_proof_are_rejected() {
        let (secret, registered) = generate_secret();
        let (_, other) = generate_secret();
        let proof = compute_write_proof(
            &secret,
            "single-main",
            "control-systems",
            "message",
            "hello",
            None,
            "nonce-1",
        )
        .unwrap();
        assert!(!verify_write_proof(
            &other,
            &proof,
            "single-main",
            "control-systems",
            "message",
            "hello",
            None,
            "nonce-1",
        ));
        assert!(!validate_secret("not-a-secret"));
        assert!(!verify_write_proof(
            &registered,
            "not-base64url",
            "single-main",
            "control-systems",
            "message",
            "hello",
            None,
            "nonce-1",
        ));
    }

    #[test]
    fn generated_secret_authenticates_private_read() {
        let (secret, registered) = generate_secret();
        let proof = compute_read_proof(&secret, "single-main", "control-systems", 7, 50).unwrap();
        assert!(verify_read_proof(
            &registered,
            &proof,
            "single-main",
            "control-systems",
            7,
            50,
        ));
        assert!(!verify_read_proof(
            &registered,
            &proof,
            "single-main",
            "other",
            7,
            50,
        ));
    }
}
