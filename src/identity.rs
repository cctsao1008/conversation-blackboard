use std::sync::OnceLock;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};
use regex::Regex;
use rusqlite::{params, Connection, OptionalExtension, Result};
use sha2::{Digest, Sha256};

use crate::{model::Identity, signed_auth, web_auth};

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

pub fn get_web_participant_role(conn: &Connection, participant_id: &str) -> Result<Option<String>> {
    if validate_participant_id(participant_id).is_none() {
        return Ok(None);
    }
    conn.query_row(
        "SELECT role FROM web_participants WHERE participant_id = ?1 LIMIT 1",
        [participant_id],
        |row| row.get(0),
    )
    .optional()
}

pub fn get_web_participant_status(
    conn: &Connection,
    participant_id: &str,
) -> Result<Option<String>> {
    if validate_participant_id(participant_id).is_none() {
        return Ok(None);
    }
    conn.query_row(
        "SELECT status FROM web_participants WHERE participant_id = ?1 LIMIT 1",
        [participant_id],
        |row| row.get(0),
    )
    .optional()
}

pub fn set_web_participant_role(
    conn: &Connection,
    participant_id: &str,
    role: &str,
) -> Result<bool> {
    if validate_participant_id(participant_id).is_none() || !matches!(role, "user" | "admin") {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants\n         SET role = ?1, updated_at = unixepoch()\n         WHERE participant_id = ?2",
        params![role, participant_id],
    )?;
    Ok(changed == 1)
}

pub fn set_web_participant_status(
    conn: &Connection,
    participant_id: &str,
    status: &str,
) -> Result<bool> {
    if validate_participant_id(participant_id).is_none() || !matches!(status, "active" | "inactive")
    {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants\n         SET status = ?1, updated_at = unixepoch()\n         WHERE participant_id = ?2",
        params![status, participant_id],
    )?;
    Ok(changed == 1)
}

pub fn provision_web_participant_identity(
    conn: &Connection,
    participant_id: &str,
    source: &str,
    label: Option<&str>,
) -> Result<Option<Identity>> {
    let participant_id =
        validate_participant_id(participant_id).ok_or(rusqlite::Error::InvalidQuery)?;
    let source = validate_source(source).ok_or(rusqlite::Error::InvalidQuery)?;
    let label = normalize_label(label).map_err(|_| rusqlite::Error::InvalidQuery)?;
    if get_web_participant(conn, &participant_id)?.is_some() {
        return Ok(None);
    }
    conn.execute(
        "INSERT INTO web_participants (participant_id, source, label) VALUES (?1, ?2, ?3)",
        params![participant_id, source, label],
    )?;
    Ok(Some(Identity {
        source,
        instance: participant_id,
        label,
    }))
}

pub fn set_web_participant_totp(
    conn: &Connection,
    participant_id: &str,
    secret: &str,
) -> Result<bool> {
    if validate_participant_id(participant_id).is_none() || !web_auth::validate_totp_secret(secret)
    {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants
         SET totp_secret = ?1, totp_last_step = NULL, totp_fail_count = 0,
             totp_locked_until = NULL, updated_at = unixepoch()
         WHERE participant_id = ?2",
        params![secret, participant_id],
    )?;
    Ok(changed == 1)
}

pub fn revoke_web_participant_totp(conn: &Connection, participant_id: &str) -> Result<bool> {
    if validate_participant_id(participant_id).is_none() {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants
         SET totp_secret = NULL, totp_last_step = NULL, totp_fail_count = 0,
             totp_locked_until = NULL, updated_at = unixepoch()
         WHERE participant_id = ?1 AND totp_secret IS NOT NULL",
        [participant_id],
    )?;
    Ok(changed == 1)
}

type TotpParticipantRow = (
    String,
    Option<String>,
    String,
    Option<i64>,
    i64,
    Option<i64>,
);

pub fn authenticate_web_totp(
    conn: &Connection,
    participant_id: &str,
    code: &str,
    now: u64,
) -> Result<Option<Identity>> {
    if validate_participant_id(participant_id).is_none()
        || code.len() != web_auth::TOTP_DIGITS as usize
        || !code.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Ok(None);
    }

    let tx = conn.unchecked_transaction()?;
    let row: Option<TotpParticipantRow> = tx
        .query_row(
            "SELECT source, label, totp_secret, totp_last_step, totp_fail_count, totp_locked_until
             FROM web_participants
             WHERE participant_id = ?1
               AND status = 'active'
               AND totp_secret IS NOT NULL
             LIMIT 1",
            [participant_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                ))
            },
        )
        .optional()?;
    let Some((source, label, secret, last_step, fail_count, locked_until)) = row else {
        return Ok(None);
    };

    let now_i64 = i64::try_from(now).unwrap_or(i64::MAX);
    if locked_until.is_some_and(|until| until > now_i64) {
        return Ok(None);
    }

    if let Some(step) = web_auth::matching_totp_step(&secret, code, now) {
        let step_i64 = i64::try_from(step).unwrap_or(i64::MAX);
        if last_step.is_some_and(|last| step_i64 <= last) {
            return Ok(None);
        }
        tx.execute(
            "UPDATE web_participants
             SET totp_last_step = ?1, totp_fail_count = 0, totp_locked_until = NULL,
                 updated_at = unixepoch()
             WHERE participant_id = ?2",
            params![step_i64, participant_id],
        )?;
        tx.commit()?;
        return Ok(Some(Identity {
            source,
            instance: participant_id.to_owned(),
            label,
        }));
    }

    let effective_fail_count = if locked_until.is_some() {
        0
    } else {
        fail_count
    };
    let new_fail_count = effective_fail_count.saturating_add(1);
    if new_fail_count >= web_auth::TOTP_MAX_FAILURES {
        let locked_until = now.saturating_add(web_auth::TOTP_LOCK_SECS);
        tx.execute(
            "UPDATE web_participants
             SET totp_fail_count = 0, totp_locked_until = ?1, updated_at = unixepoch()
             WHERE participant_id = ?2",
            params![
                i64::try_from(locked_until).unwrap_or(i64::MAX),
                participant_id
            ],
        )?;
    } else {
        tx.execute(
            "UPDATE web_participants
             SET totp_fail_count = ?1, totp_locked_until = NULL, updated_at = unixepoch()
             WHERE participant_id = ?2",
            params![new_fail_count, participant_id],
        )?;
    }
    tx.commit()?;
    Ok(None)
}

pub fn get_web_participant_verification(
    conn: &Connection,
    participant_id: &str,
) -> Result<Option<ParticipantVerification>> {
    if validate_participant_id(participant_id).is_none() {
        return Ok(None);
    }
    conn.query_row(
        "SELECT source, participant_id, label, signature_scheme, public_key\n         FROM web_participants\n         WHERE participant_id = ?1\n           AND status = 'active'\n           AND signature_scheme IS NOT NULL\n           AND public_key IS NOT NULL\n         LIMIT 1",
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
    fn source_and_participant_contracts_match_reference() {
        assert!(validate_source("rotary-inverted-pendulum").is_some());
        assert!(validate_source(" bad source ").is_none());
        assert!(validate_participant_id("rotary-main").is_some());
        assert!(validate_participant_id("rotary/main").is_none());
    }

    #[test]
    fn participant_roles_are_explicit_and_mutable() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        provision_web_participant_identity(&conn, "operator-main", "operator", None)
            .unwrap()
            .unwrap();
        assert_eq!(
            get_web_participant_role(&conn, "operator-main")
                .unwrap()
                .as_deref(),
            Some("user")
        );
        assert!(set_web_participant_role(&conn, "operator-main", "admin").unwrap());
        assert_eq!(
            get_web_participant_role(&conn, "operator-main")
                .unwrap()
                .as_deref(),
            Some("admin")
        );
    }

    #[test]
    fn participant_lifecycle_disables_totp_and_signing_without_erasing_credentials() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        provision_web_participant_identity(&conn, "lifecycle-main", "test", None)
            .unwrap()
            .unwrap();

        let secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";
        assert!(set_web_participant_totp(&conn, "lifecycle-main", secret).unwrap());
        let (_, public_key) = signed_auth::generate_keypair();
        assert!(set_web_participant_signing_key(&conn, "lifecycle-main", &public_key).unwrap());

        assert_eq!(
            get_web_participant_status(&conn, "lifecycle-main")
                .unwrap()
                .as_deref(),
            Some("active")
        );
        assert!(authenticate_web_totp(&conn, "lifecycle-main", "287082", 59)
            .unwrap()
            .is_some());
        assert!(get_web_participant_verification(&conn, "lifecycle-main")
            .unwrap()
            .is_some());

        assert!(set_web_participant_status(&conn, "lifecycle-main", "inactive").unwrap());
        assert!(authenticate_web_totp(&conn, "lifecycle-main", "359152", 89)
            .unwrap()
            .is_none());
        assert!(get_web_participant_verification(&conn, "lifecycle-main")
            .unwrap()
            .is_none());

        let stored: (Option<String>, Option<String>, Option<String>) = conn
            .query_row(
                "SELECT totp_secret, signature_scheme, public_key\n                 FROM web_participants WHERE participant_id = 'lifecycle-main'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        assert_eq!(stored.0.as_deref(), Some(secret));
        assert_eq!(stored.1.as_deref(), Some(signed_auth::SIGNATURE_SCHEME));
        assert_eq!(stored.2.as_deref(), Some(public_key.as_str()));

        assert!(set_web_participant_status(&conn, "lifecycle-main", "active").unwrap());
        assert!(authenticate_web_totp(&conn, "lifecycle-main", "359152", 89)
            .unwrap()
            .is_some());
        assert!(get_web_participant_verification(&conn, "lifecycle-main")
            .unwrap()
            .is_some());
    }

    #[test]
    fn bearer_rotate_and_revoke_match_reference_semantics() {
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
    }

    #[test]
    fn signing_key_registry_is_rotatable_and_revocable() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        provision_web_participant_identity(&conn, "claude-main", "claude", Some("Claude"))
            .unwrap()
            .unwrap();
        let (_, public_a) = signed_auth::generate_keypair();
        let (_, public_b) = signed_auth::generate_keypair();
        assert!(set_web_participant_signing_key(&conn, "claude-main", &public_a).unwrap());
        let registered = get_web_participant_verification(&conn, "claude-main")
            .unwrap()
            .unwrap();
        assert_eq!(registered.identity.instance, "claude-main");
        assert_eq!(registered.public_key, public_a);
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
    }
}
