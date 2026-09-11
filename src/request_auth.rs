use axum::http::{header, HeaderMap};
use rusqlite::{Connection, OptionalExtension, Result};

use crate::{identity, model::Identity};

/// Legacy marker retained so a stale/incomplete participant credential cannot
/// silently downgrade to bearer authentication. Participant identity itself is
/// resolved from the private key.
pub const PARTICIPANT_ID_HEADER: &str = "x-blackboard-participant-id";
pub const PRIVATE_KEY_HEADER: &str = "x-blackboard-private-key";

pub fn resolve_request_identity(
    conn: &Connection,
    headers: &HeaderMap,
) -> Result<Option<Identity>> {
    if headers.contains_key(PRIVATE_KEY_HEADER) {
        let Some(private_key) = header_text(headers, PRIVATE_KEY_HEADER) else {
            return Ok(None);
        };
        return resolve_web_participant_by_key(conn, private_key);
    }

    // Preserve the old no-downgrade behavior for malformed legacy requests:
    // a participant-id header without its credential is not a bearer request.
    if headers.contains_key(PARTICIPANT_ID_HEADER) {
        return Ok(None);
    }

    let Some(token) = bearer_token(headers) else {
        return Ok(None);
    };
    identity::resolve_identity(conn, token)
}

fn resolve_web_participant_by_key(
    conn: &Connection,
    private_key: &str,
) -> Result<Option<Identity>> {
    if !identity::validate_private_key(private_key) {
        return Ok(None);
    }

    conn.query_row(
        "SELECT source, participant_id, label\n         FROM web_participants\n         WHERE key_hash = ?1\n         LIMIT 1",
        [identity::hash_token(private_key)],
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

fn header_text<'a>(headers: &'a HeaderMap, name: &'static str) -> Option<&'a str> {
    headers
        .get(name)?
        .to_str()
        .ok()
        .filter(|value| !value.is_empty())
}

fn bearer_token(headers: &HeaderMap) -> Option<&str> {
    let value = headers.get(header::AUTHORIZATION)?.to_str().ok()?;
    let (scheme, token) = value.split_once(' ')?;
    if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() {
        return None;
    }
    Some(token)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db;
    use tempfile::tempdir;

    #[test]
    fn private_key_is_self_identifying_and_bearer_remains_compatible() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();

        identity::provision_web_participant(
            &conn,
            "operator-main",
            "operator",
            Some("Operator browser"),
            "wk_operator_secret",
        )
        .unwrap()
        .unwrap();
        identity::provision_web_participant(
            &conn,
            "other-main",
            "other",
            Some("Other browser"),
            "wk_other_secret",
        )
        .unwrap()
        .unwrap();
        let (_, bearer) =
            identity::register_identity(&conn, "rest-client", Some("REST client")).unwrap();

        let mut private_key_headers = HeaderMap::new();
        private_key_headers.insert(PRIVATE_KEY_HEADER, "wk_operator_secret".parse().unwrap());
        let resolved = resolve_request_identity(&conn, &private_key_headers)
            .unwrap()
            .unwrap();
        assert_eq!(resolved.source, "operator");
        assert_eq!(resolved.instance, "operator-main");

        // The credential is authoritative. A legacy participant-id claim does
        // not select or override the identity resolved from the key.
        private_key_headers.insert(PARTICIPANT_ID_HEADER, "other-main".parse().unwrap());
        let resolved = resolve_request_identity(&conn, &private_key_headers)
            .unwrap()
            .unwrap();
        assert_eq!(resolved.source, "operator");
        assert_eq!(resolved.instance, "operator-main");

        private_key_headers.insert(PRIVATE_KEY_HEADER, "wrong-key".parse().unwrap());
        assert!(resolve_request_identity(&conn, &private_key_headers)
            .unwrap()
            .is_none());

        // Supplying an invalid participant credential must not fall back to a
        // valid bearer token from another identity.
        private_key_headers.insert(PRIVATE_KEY_HEADER, "wrong-key".parse().unwrap());
        private_key_headers.insert(
            header::AUTHORIZATION,
            format!("Bearer {bearer}").parse().unwrap(),
        );
        assert!(resolve_request_identity(&conn, &private_key_headers)
            .unwrap()
            .is_none());

        let mut legacy_incomplete = HeaderMap::new();
        legacy_incomplete.insert(PARTICIPANT_ID_HEADER, "operator-main".parse().unwrap());
        legacy_incomplete.insert(
            header::AUTHORIZATION,
            format!("Bearer {bearer}").parse().unwrap(),
        );
        assert!(resolve_request_identity(&conn, &legacy_incomplete)
            .unwrap()
            .is_none());

        let mut bearer_headers = HeaderMap::new();
        bearer_headers.insert(
            header::AUTHORIZATION,
            format!("Bearer {bearer}").parse().unwrap(),
        );
        assert_eq!(
            resolve_request_identity(&conn, &bearer_headers)
                .unwrap()
                .unwrap()
                .source,
            "rest-client"
        );
    }

    #[test]
    fn rotation_and_revocation_change_key_authority_immediately() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();

        identity::provision_web_participant(&conn, "operator-main", "operator", None, "wk_old")
            .unwrap()
            .unwrap();

        let headers_for = |key: &'static str| {
            let mut headers = HeaderMap::new();
            headers.insert(PRIVATE_KEY_HEADER, key.parse().unwrap());
            headers
        };

        assert!(resolve_request_identity(&conn, &headers_for("wk_old"))
            .unwrap()
            .is_some());
        identity::rotate_web_participant_key(&conn, "operator-main", "wk_new").unwrap();
        assert!(resolve_request_identity(&conn, &headers_for("wk_old"))
            .unwrap()
            .is_none());
        assert_eq!(
            resolve_request_identity(&conn, &headers_for("wk_new"))
                .unwrap()
                .unwrap()
                .instance,
            "operator-main"
        );

        identity::revoke_web_participant_key(&conn, "operator-main").unwrap();
        assert!(resolve_request_identity(&conn, &headers_for("wk_new"))
            .unwrap()
            .is_none());
    }
}
