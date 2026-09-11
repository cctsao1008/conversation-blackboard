use std::sync::OnceLock;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};
use regex::Regex;
use rusqlite::{params, Connection, OptionalExtension, Result};
use sha2::{Digest, Sha256};

use crate::{model::Identity, participant_key, signed_auth};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ParticipantVerification {
    pub identity: Identity,
    pub signature_scheme: String,
    pub public_key: String,
}

fn source_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$").unwrap())
}

fn participant_id_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$").unwrap())
}

pub fn hash_token(token: &str) -> String {
    let digest = Sha256::digest(token.as_bytes());
    let mut out = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(&mut out, "{byte:02x}");
    }
    out
}

pub fn resolve_identity(conn: &Connection, token: &str) -> Result<Option<Identity>> {
    if token.is_empty() {
        return Ok(None);
    }
    conn.query_row(
        "SELECT source, instance, label FROM identities WHERE token_hash = ?1 LIMIT 1",
        [hash_token(token)],
        |row| {
            Ok(Identity {
                source: row.get(0)?,
                instance: row.get(1)?,
                label: row.get(2)?,
            })
        },
    )
    .optional()
}

pub fn resolve_web_participant(
    conn: &Connection,
    participant_id: &str,
    private_key: &str,
) -> Result<Option<Identity>> {
    if validate_participant_id(participant_id).is_none() || !validate_private_key(private_key) {
        return Ok(None);
    }
    conn.query_row(
        "SELECT source, participant_id, label\n         FROM web_participants\n         WHERE participant_id = ?1 AND key_hash = ?2\n         LIMIT 1",
        params![participant_id, hash_token(private_key)],
        |row| {
            Ok(Identity {
                source: row.get(0)?,
                instance: row.get(1)?,
                label: row.get(2)?,
            })
        },
    )
    .optional()
}

pub fn get_identity(conn: &Connection, instance: &str) -> Result<Option<Identity>> {
    conn.query_row(
        "SELECT source, instance, label FROM identities WHERE instance = ?1 LIMIT 1",
        [instance],
        |row| {
            Ok(Identity {
                source: row.get(0)?,
                instance: row.get(1)?,
                label: row.get(2)?,
            })
        },
    )
    .optional()
}

pub fn get_web_participant(conn: &Connection, participant_id: &str) -> Result<Option<Identity>> {
    conn.query_row(
        "SELECT source, participant_id, label\n         FROM web_participants\n         WHERE participant_id = ?1\n         LIMIT 1",
        [participant_id],
        |row| {
            Ok(Identity {
                source: row.get(0)?,
                instance: row.get(1)?,
                label: row.get(2)?,
            })
        },
    )
    .optional()
}

pub fn get_web_participant_verification(
    conn: &Connection,
    participant_id: &str,
) -> Result<Option<ParticipantVerification>> {
    if validate_participant_id(participant_id).is_none() {
        return Ok(None);
    }
    conn.query_row(
        "SELECT source, participant_id, label, signature_scheme, public_key\n         FROM web_participants\n         WHERE participant_id = ?1\n           AND signature_scheme IS NOT NULL\n           AND public_key IS NOT NULL\n         LIMIT 1",
        [participant_id],
        |row| {
            Ok(ParticipantVerification {
                identity: Identity {
                    source: row.get(0)?,
                    instance: row.get(1)?,
                    label: row.get(2)?,
                },
                signature_scheme: row.get(3)?,
                public_key: row.get(4)?,
            })
        },
    )
    .optional()
}

pub fn set_web_participant_signing_key(
    conn: &Connection,
    participant_id: &str,
    public_key: &str,
) -> Result<bool> {
    if validate_participant_id(participant_id).is_none()
        || !signed_auth::validate_public_key(public_key)
    {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants\n         SET public_key = ?1, signature_scheme = ?2, updated_at = unixepoch()\n         WHERE participant_id = ?3",
        params![public_key, signed_auth::SIGNATURE_SCHEME, participant_id],
    )?;
    Ok(changed == 1)
}

pub fn revoke_web_participant_signing_key(conn: &Connection, participant_id: &str) -> Result<bool> {
    if validate_participant_id(participant_id).is_none() {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants\n         SET public_key = NULL, signature_scheme = NULL, updated_at = unixepoch()\n         WHERE participant_id = ?1 AND public_key IS NOT NULL",
        [participant_id],
    )?;
    Ok(changed == 1)
}

pub fn validate_source(source: &str) -> Option<String> {
    let source = source.trim();
    if source_re().is_match(source) {
        Some(source.to_owned())
    } else {
        None
    }
}

pub fn validate_participant_id(participant_id: &str) -> Option<String> {
    let participant_id = participant_id.trim();
    if participant_id_re().is_match(participant_id) {
        Some(participant_id.to_owned())
    } else {
        None
    }
}

pub fn validate_private_key(private_key: &str) -> bool {
    let length = private_key.chars().count();
    (1..=256).contains(&length)
        && !private_key
            .chars()
            .any(|value| value.is_control() || value.is_whitespace())
}

pub fn normalize_label(label: Option<&str>) -> Result<Option<String>, &'static str> {
    let label = label.map(str::trim).filter(|value| !value.is_empty());
    match label {
        Some(value) if value.chars().count() > 256 => Err("invalid label"),
        Some(value) => Ok(Some(value.to_owned())),
        None => Ok(None),
    }
}

pub fn register_identity(
    conn: &Connection,
    source: &str,
    label: Option<&str>,
) -> Result<(Identity, String)> {
    let source = validate_source(source).ok_or(rusqlite::Error::InvalidQuery)?;
    let label = normalize_label(label).map_err(|_| rusqlite::Error::InvalidQuery)?;

    loop {
        let instance = new_instance_id();
        let token = new_token();
        let result = conn.execute(
            "INSERT INTO identities (instance, source, label, token_hash) VALUES (?1, ?2, ?3, ?4)",
            params![instance, source, label, hash_token(&token)],
        );
        match result {
            Ok(_) => {
                return Ok((
                    Identity {
                        source: source.clone(),
                        instance,
                        label: label.clone(),
                    },
                    token,
                ))
            }
            Err(rusqlite::Error::SqliteFailure(err, _))
                if err.code == rusqlite::ErrorCode::ConstraintViolation => {}
            Err(err) => return Err(err),
        }
    }
}

pub fn rotate_token(conn: &Connection, instance: &str) -> Result<Option<String>> {
    let token = new_token();
    let changed = conn.execute(
        "UPDATE identities SET token_hash = ?1 WHERE instance = ?2",
        params![hash_token(&token), instance],
    )?;
    if changed == 1 {
        Ok(Some(token))
    } else {
        Ok(None)
    }
}

pub fn revoke_token(conn: &Connection, instance: &str) -> Result<bool> {
    let changed = conn.execute(
        "UPDATE identities SET token_hash = NULL WHERE instance = ?1",
        [instance],
    )?;
    Ok(changed == 1)
}

pub fn provision_web_participant(
    conn: &Connection,
    participant_id: &str,
    source: &str,
    label: Option<&str>,
    private_key: &str,
) -> Result<Option<Identity>> {
    let participant_id =
        validate_participant_id(participant_id).ok_or(rusqlite::Error::InvalidQuery)?;
    let source = validate_source(source).ok_or(rusqlite::Error::InvalidQuery)?;
    let label = normalize_label(label).map_err(|_| rusqlite::Error::InvalidQuery)?;
    if !validate_private_key(private_key) {
        return Err(rusqlite::Error::InvalidQuery);
    }
    if get_web_participant(conn, &participant_id)?.is_some() {
        return Ok(None);
    }

    conn.execute(
        "INSERT INTO web_participants (participant_id, source, label, key_hash)\n         VALUES (?1, ?2, ?3, ?4)",
        params![participant_id, source, label, hash_token(private_key)],
    )?;
    Ok(Some(Identity {
        source,
        instance: participant_id,
        label,
    }))
}

pub fn rotate_web_participant_key(
    conn: &Connection,
    participant_id: &str,
    private_key: &str,
) -> Result<bool> {
    if validate_participant_id(participant_id).is_none() || !validate_private_key(private_key) {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants\n         SET key_hash = ?1, updated_at = unixepoch()\n         WHERE participant_id = ?2",
        params![hash_token(private_key), participant_id],
    )?;
    Ok(changed == 1)
}

pub fn revoke_web_participant_key(conn: &Connection, participant_id: &str) -> Result<bool> {
    let changed = conn.execute(
        "UPDATE web_participants\n         SET key_hash = NULL, updated_at = unixepoch()\n         WHERE participant_id = ?1 AND key_hash IS NOT NULL",
        [participant_id],
    )?;
    Ok(changed == 1)
}

pub fn new_web_private_key() -> String {
    participant_key::generate_random_private_key()
}

fn new_instance_id() -> String {
    let mut bytes = [0_u8; 6];
    OsRng.fill_bytes(&mut bytes);
    let mut out = String::from("i-");
    for byte in bytes {
        use std::fmt::Write as _;
        let _ = write!(&mut out, "{byte:02x}");
    }
    out
}

fn new_token() -> String {
    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db;
    use tempfile::tempdir;

    #[test]
    fn sha256_matches_python_contract() {
        assert_eq!(
            hash_token("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn source_and_participant_contracts_match_reference() {
        assert!(validate_source("rotary-inverted-pendulum").is_some());
        assert!(validate_source(" bad source ").is_none());
        assert!(validate_participant_id("rotary-main").is_some());
        assert!(validate_participant_id("rotary/main").is_none());
        assert!(validate_private_key("mini-rsa-d-123-n-456"));
        assert!(!validate_private_key("key with spaces"));
    }

    #[test]
    fn rotate_and_revoke_match_reference_semantics() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        let (identity, original) = register_identity(&conn, "test-source", Some("test")).unwrap();
        assert!(resolve_identity(&conn, &original).unwrap().is_some());

        let rotated = rotate_token(&conn, &identity.instance).unwrap().unwrap();
        assert!(resolve_identity(&conn, &original).unwrap().is_none());
        assert_eq!(
            resolve_identity(&conn, &rotated).unwrap().unwrap().instance,
            identity.instance
        );

        assert!(revoke_token(&conn, &identity.instance).unwrap());
        assert!(resolve_identity(&conn, &rotated).unwrap().is_none());
        assert!(rotate_token(&conn, "missing-instance").unwrap().is_none());
        assert!(!revoke_token(&conn, "missing-instance").unwrap());
    }

    #[test]
    fn web_participant_key_is_independent_rotatable_and_server_resolved() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        let (_, bearer) = register_identity(&conn, "single", Some("rest-single")).unwrap();

        let participant = provision_web_participant(
            &conn,
            "single-main",
            "single",
            Some("Single main conversation"),
            "prompt-key-001",
        )
        .unwrap()
        .unwrap();
        assert_eq!(participant.instance, "single-main");
        assert_eq!(participant.source, "single");
        assert_eq!(
            resolve_web_participant(&conn, "single-main", "prompt-key-001")
                .unwrap()
                .unwrap()
                .instance,
            "single-main"
        );
        assert!(resolve_web_participant(&conn, "single-main", "wrong-key")
            .unwrap()
            .is_none());
        assert!(
            resolve_web_participant(&conn, "wrong-participant", "prompt-key-001")
                .unwrap()
                .is_none()
        );
        assert!(resolve_identity(&conn, "prompt-key-001").unwrap().is_none());
        assert!(resolve_web_participant(&conn, "single-main", &bearer)
            .unwrap()
            .is_none());

        assert!(rotate_web_participant_key(&conn, "single-main", "prompt-key-002").unwrap());
        assert!(
            resolve_web_participant(&conn, "single-main", "prompt-key-001")
                .unwrap()
                .is_none()
        );
        assert_eq!(
            resolve_web_participant(&conn, "single-main", "prompt-key-002")
                .unwrap()
                .unwrap()
                .instance,
            "single-main"
        );

        assert!(revoke_web_participant_key(&conn, "single-main").unwrap());
        assert!(
            resolve_web_participant(&conn, "single-main", "prompt-key-002")
                .unwrap()
                .is_none()
        );
        assert!(resolve_identity(&conn, &bearer).unwrap().is_some());
    }

    #[test]
    fn signing_key_registry_is_rotatable_revocable_and_independent_of_bearer_key() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        provision_web_participant(&conn, "claude-main", "claude", Some("Claude"), "legacy-key")
            .unwrap()
            .unwrap();

        let (_, public_a) = signed_auth::generate_keypair();
        let (_, public_b) = signed_auth::generate_keypair();
        assert!(set_web_participant_signing_key(&conn, "claude-main", &public_a).unwrap());
        let registered = get_web_participant_verification(&conn, "claude-main")
            .unwrap()
            .unwrap();
        assert_eq!(registered.identity.instance, "claude-main");
        assert_eq!(registered.identity.source, "claude");
        assert_eq!(registered.signature_scheme, signed_auth::SIGNATURE_SCHEME);
        assert_eq!(registered.public_key, public_a);
        assert!(resolve_web_participant(&conn, "claude-main", "legacy-key")
            .unwrap()
            .is_some());

        assert!(set_web_participant_signing_key(&conn, "claude-main", &public_b).unwrap());
        assert_eq!(
            get_web_participant_verification(&conn, "claude-main")
                .unwrap()
                .unwrap()
                .public_key,
            public_b
        );

        assert!(revoke_web_participant_signing_key(&conn, "claude-main").unwrap());
        assert!(get_web_participant_verification(&conn, "claude-main")
            .unwrap()
            .is_none());
        assert!(resolve_web_participant(&conn, "claude-main", "legacy-key")
            .unwrap()
            .is_some());
    }
}
