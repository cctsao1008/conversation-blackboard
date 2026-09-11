use axum::http::{header, HeaderMap};
use rusqlite::{Connection, Result};

use crate::{identity, model::Identity, signed_auth, web_auth};

pub const PARTICIPANT_ID_HEADER: &str = "x-blackboard-participant-id";
pub const SIGNATURE_SCHEME_HEADER: &str = "x-blackboard-signature-scheme";
pub const SIGNATURE_HEADER: &str = "x-blackboard-signature";
const RETIRED_PRIVATE_KEY_HEADER: &str = "x-blackboard-private-key";

pub fn verified_web_session(headers: &HeaderMap) -> Option<web_auth::WebSession> {
    let token = header_text(headers, web_auth::WEB_SESSION_HEADER)?;
    web_auth::verify_web_session(token)
}

pub fn resolve_request_identity_for_target(
    conn: &Connection,
    headers: &HeaderMap,
    method: &str,
    request_target: &str,
) -> Result<Option<Identity>> {
    let signed_attempt =
        headers.contains_key(SIGNATURE_HEADER) || headers.contains_key(SIGNATURE_SCHEME_HEADER);
    if !signed_attempt {
        return resolve_request_identity(conn, headers);
    }
    if headers.contains_key(RETIRED_PRIVATE_KEY_HEADER)
        || headers.contains_key(web_auth::WEB_SESSION_HEADER)
    {
        return Ok(None);
    }

    let Some(participant_id) = header_text(headers, PARTICIPANT_ID_HEADER) else {
        return Ok(None);
    };
    let Some(scheme) = header_text(headers, SIGNATURE_SCHEME_HEADER) else {
        return Ok(None);
    };
    let Some(signature) = header_text(headers, SIGNATURE_HEADER) else {
        return Ok(None);
    };
    if scheme != signed_auth::SIGNATURE_SCHEME {
        return Ok(None);
    }
    let Some(verification) = identity::get_web_participant_verification(conn, participant_id)?
    else {
        return Ok(None);
    };
    if verification.signature_scheme != scheme
        || !web_auth::verify_http_request_signature(
            &verification.public_key,
            signature,
            participant_id,
            method,
            request_target,
        )
    {
        return Ok(None);
    }
    Ok(Some(verification.identity))
}

pub fn resolve_request_identity(
    conn: &Connection,
    headers: &HeaderMap,
) -> Result<Option<Identity>> {
    if headers.contains_key(RETIRED_PRIVATE_KEY_HEADER) {
        return Ok(None);
    }

    if headers.contains_key(web_auth::WEB_SESSION_HEADER) {
        let Some(session) = verified_web_session(headers) else {
            return Ok(None);
        };
        if session.session_type != web_auth::WebSessionKind::HumanWeb {
            return Ok(None);
        }
        return identity::get_web_participant(conn, &session.participant_id);
    }

    if headers.contains_key(PARTICIPANT_ID_HEADER)
        || headers.contains_key(SIGNATURE_HEADER)
        || headers.contains_key(SIGNATURE_SCHEME_HEADER)
    {
        return Ok(None);
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
    fn web_session_and_bearer_resolve_without_raw_participant_keys() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        identity::provision_web_participant_identity(
            &conn,
            "operator-main",
            "operator",
            Some("Operator browser"),
        )
        .unwrap()
        .unwrap();
        let (_, bearer) =
            identity::register_identity(&conn, "rest-client", Some("REST client")).unwrap();

        let session = web_auth::issue_web_session("operator-main");
        let mut headers = HeaderMap::new();
        headers.insert(web_auth::WEB_SESSION_HEADER, session.token.parse().unwrap());
        let resolved = resolve_request_identity(&conn, &headers).unwrap().unwrap();
        assert_eq!(resolved.instance, "operator-main");

        let guest = web_auth::issue_guest_session();
        let mut guest_headers = HeaderMap::new();
        guest_headers.insert(web_auth::WEB_SESSION_HEADER, guest.token.parse().unwrap());
        assert!(resolve_request_identity(&conn, &guest_headers)
            .unwrap()
            .is_none());
        assert_eq!(
            verified_web_session(&guest_headers).unwrap().session_type,
            web_auth::WebSessionKind::Guest
        );

        let mut retired = HeaderMap::new();
        retired.insert(RETIRED_PRIVATE_KEY_HEADER, "old-key".parse().unwrap());
        retired.insert(
            header::AUTHORIZATION,
            format!("Bearer {bearer}").parse().unwrap(),
        );
        assert!(resolve_request_identity(&conn, &retired).unwrap().is_none());

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
}
