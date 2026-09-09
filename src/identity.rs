use std::sync::OnceLock;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};
use regex::Regex;
use rusqlite::{params, Connection, OptionalExtension, Result};
use sha2::{Digest, Sha256};

use crate::model::Identity;

fn source_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$").unwrap())
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

pub fn validate_source(source: &str) -> Option<String> {
    let source = source.trim();
    if source_re().is_match(source) {
        Some(source.to_owned())
    } else {
        None
    }
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
    fn source_contract_matches_reference() {
        assert!(validate_source("rotary-inverted-pendulum").is_some());
        assert!(validate_source(" bad source ").is_none());
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
}
