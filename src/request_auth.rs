use axum::http::{header, HeaderMap};
use rusqlite::{Connection, Result};

use crate::{identity, model::Identity};

pub const PARTICIPANT_ID_HEADER: &str = "x-blackboard-participant-id";
pub const PRIVATE_KEY_HEADER: &str = "x-blackboard-private-key";

pub fn resolve_request_identity(
    conn: &Connection,
    headers: &HeaderMap,
) -> Result<Option<Identity>> {
    let participant_header_present =
        headers.contains_key(PARTICIPANT_ID_HEADER) || headers.contains_key(PRIVATE_KEY_HEADER);

    if participant_header_present {
        let Some(participant_id) = header_text(headers, PARTICIPANT_ID_HEADER) else {
            return Ok(None);
        };
        let Some(private_key) = header_text(headers, PRIVATE_KEY_HEADER) else {
            return Ok(None);
        };
        return identity::resolve_web_participant(conn, participant_id, private_key);
    }

    let Some(token) = bearer_token(headers) else {
        return Ok(None);
    };
    identity::resolve_identity(conn, token)
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
    fn participant_credentials_are_possession_based_and_bearer_remains_compatible() {
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
        let (_, bearer) =
            identity::register_identity(&conn, "rest-client", Some("REST client")).unwrap();

        let mut participant_headers = HeaderMap::new();
        participant_headers.insert(PARTICIPANT_ID_HEADER, "operator-main".parse().unwrap());
        participant_headers.insert(PRIVATE_KEY_HEADER, "wk_operator_secret".parse().unwrap());
        let resolved = resolve_request_identity(&conn, &participant_headers)
            .unwrap()
            .unwrap();
        assert_eq!(resolved.source, "operator");
        assert_eq!(resolved.instance, "operator-main");

        participant_headers.insert(PRIVATE_KEY_HEADER, "wrong-key".parse().unwrap());
        assert!(resolve_request_identity(&conn, &participant_headers)
            .unwrap()
            .is_none());

        let mut incomplete = HeaderMap::new();
        incomplete.insert(PARTICIPANT_ID_HEADER, "operator-main".parse().unwrap());
        incomplete.insert(
            header::AUTHORIZATION,
            format!("Bearer {bearer}").parse().unwrap(),
        );
        assert!(resolve_request_identity(&conn, &incomplete)
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
    fn rotation_and_revocation_change_credential_authority_immediately() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();

        identity::provision_web_participant(&conn, "operator-main", "operator", None, "wk_old")
            .unwrap()
            .unwrap();

        let headers_for = |key: &'static str| {
            let mut headers = HeaderMap::new();
            headers.insert(PARTICIPANT_ID_HEADER, "operator-main".parse().unwrap());
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
        assert!(resolve_request_identity(&conn, &headers_for("wk_new"))
            .unwrap()
            .is_some());

        identity::revoke_web_participant_key(&conn, "operator-main").unwrap();
        assert!(resolve_request_identity(&conn, &headers_for("wk_new"))
            .unwrap()
            .is_none());
    }
}
