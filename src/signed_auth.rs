use std::collections::BTreeMap;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use ring::{
    rand::SystemRandom,
    signature::{Ed25519KeyPair, KeyPair, UnparsedPublicKey, ED25519},
};
use serde_json::{json, Value};

pub const SIGNATURE_SCHEME: &str = "ed25519-v1";
pub const PUBLIC_KEY_PREFIX: &str = "ed25519-pk:";
pub const PRIVATE_KEY_PREFIX: &str = "ed25519-sk:";

pub fn generate_keypair() -> (String, String) {
    let rng = SystemRandom::new();
    let pkcs8 = Ed25519KeyPair::generate_pkcs8(&rng)
        .expect("operating-system RNG must be available for Ed25519 key generation");
    let keypair = Ed25519KeyPair::from_pkcs8(pkcs8.as_ref())
        .expect("freshly generated Ed25519 PKCS#8 must parse");

    let private_key = format!(
        "{PRIVATE_KEY_PREFIX}{}",
        URL_SAFE_NO_PAD.encode(pkcs8.as_ref())
    );
    let public_key = format!(
        "{PUBLIC_KEY_PREFIX}{}",
        URL_SAFE_NO_PAD.encode(keypair.public_key().as_ref())
    );
    (private_key, public_key)
}

pub fn validate_public_key(public_key: &str) -> bool {
    decode_public_key(public_key).is_some()
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
    payload.insert("body".into(), json!(body));
    payload.insert("channel".into(), json!(channel));
    payload.insert("kind".into(), json!(kind));
    payload.insert("nonce".into(), json!(nonce));
    payload.insert("participant_id".into(), json!(participant_id));
    payload.insert("reply_to".into(), json!(reply_to));
    payload.insert("signature_version".into(), json!(SIGNATURE_SCHEME));
    serde_json::to_vec(&payload).expect("canonical signed-write payload must serialize")
}

#[cfg(test)]
#[allow(clippy::too_many_arguments)]
pub fn sign_write(
    private_key: &str,
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> Option<String> {
    let pkcs8 = decode_prefixed(private_key, PRIVATE_KEY_PREFIX)?;
    let keypair = Ed25519KeyPair::from_pkcs8(&pkcs8).ok()?;
    let canonical = canonical_write_bytes(participant_id, channel, kind, body, reply_to, nonce);
    Some(URL_SAFE_NO_PAD.encode(keypair.sign(&canonical).as_ref()))
}

pub fn verify_message_signature(public_key: &str, signature: &str, message: &[u8]) -> bool {
    let Some(public_key) = decode_public_key(public_key) else {
        return false;
    };
    let Ok(signature) = URL_SAFE_NO_PAD.decode(signature) else {
        return false;
    };
    if signature.len() != 64 {
        return false;
    }

    UnparsedPublicKey::new(&ED25519, public_key)
        .verify(message, &signature)
        .is_ok()
}

#[cfg(test)]
pub fn sign_message_signature(private_key: &str, message: &[u8]) -> Option<String> {
    let pkcs8 = decode_prefixed(private_key, PRIVATE_KEY_PREFIX)?;
    let keypair = Ed25519KeyPair::from_pkcs8(&pkcs8).ok()?;
    Some(URL_SAFE_NO_PAD.encode(keypair.sign(message).as_ref()))
}

#[allow(clippy::too_many_arguments)]
pub fn verify_write_signature(
    public_key: &str,
    signature: &str,
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> bool {
    let canonical = canonical_write_bytes(participant_id, channel, kind, body, reply_to, nonce);
    verify_message_signature(public_key, signature, &canonical)
}

fn decode_public_key(public_key: &str) -> Option<Vec<u8>> {
    let decoded = decode_prefixed(public_key, PUBLIC_KEY_PREFIX)?;
    (decoded.len() == 32).then_some(decoded)
}

fn decode_prefixed(value: &str, prefix: &str) -> Option<Vec<u8>> {
    let encoded = value.strip_prefix(prefix)?;
    if encoded.is_empty() {
        return None;
    }
    URL_SAFE_NO_PAD.decode(encoded).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_keypair_signs_and_verifies_canonical_write() {
        let (private_key, public_key) = generate_keypair();
        assert!(private_key.starts_with(PRIVATE_KEY_PREFIX));
        assert!(validate_public_key(&public_key));

        let signature = sign_write(
            &private_key,
            "claude-main",
            "blackboard-lounge",
            "banter",
            "hello",
            None,
            "claude-001",
        )
        .unwrap();
        assert!(verify_write_signature(
            &public_key,
            &signature,
            "claude-main",
            "blackboard-lounge",
            "banter",
            "hello",
            None,
            "claude-001",
        ));
    }

    #[test]
    fn signature_binds_every_persisted_write_field() {
        let (private_key, public_key) = generate_keypair();
        let signature = sign_write(
            &private_key,
            "single-main",
            "control-systems",
            "insight",
            "payload",
            Some(7),
            "single-001",
        )
        .unwrap();

        assert!(!verify_write_signature(
            &public_key,
            &signature,
            "rotary-main",
            "control-systems",
            "insight",
            "payload",
            Some(7),
            "single-001",
        ));
        assert!(!verify_write_signature(
            &public_key,
            &signature,
            "single-main",
            "other-channel",
            "insight",
            "payload",
            Some(7),
            "single-001",
        ));
        assert!(!verify_write_signature(
            &public_key,
            &signature,
            "single-main",
            "control-systems",
            "message",
            "payload",
            Some(7),
            "single-001",
        ));
        assert!(!verify_write_signature(
            &public_key,
            &signature,
            "single-main",
            "control-systems",
            "insight",
            "tampered",
            Some(7),
            "single-001",
        ));
        assert!(!verify_write_signature(
            &public_key,
            &signature,
            "single-main",
            "control-systems",
            "insight",
            "payload",
            None,
            "single-001",
        ));
        assert!(!verify_write_signature(
            &public_key,
            &signature,
            "single-main",
            "control-systems",
            "insight",
            "payload",
            Some(7),
            "single-002",
        ));
    }

    #[test]
    fn wrong_key_and_malformed_material_are_rejected() {
        let (private_key, public_key) = generate_keypair();
        let (_, other_public_key) = generate_keypair();
        let signature = sign_write(
            &private_key,
            "single-main",
            "control-systems",
            "message",
            "hello",
            None,
            "nonce-1",
        )
        .unwrap();

        assert!(!verify_write_signature(
            &other_public_key,
            &signature,
            "single-main",
            "control-systems",
            "message",
            "hello",
            None,
            "nonce-1",
        ));
        assert!(!validate_public_key("not-a-public-key"));
        assert!(!verify_write_signature(
            &public_key,
            "not-base64url",
            "single-main",
            "control-systems",
            "message",
            "hello",
            None,
            "nonce-1",
        ));
    }
}
