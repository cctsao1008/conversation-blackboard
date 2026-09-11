use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use ring::signature::Ed25519KeyPair;
use tempfile::tempdir;
use tower::ServiceExt;

use crate::{db, http, identity, request_auth, signed_auth, web_auth};

#[tokio::test]
async fn admin_role_does_not_turn_valid_agent_signature_into_admin_authority() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();

    identity::provision_web_participant_identity(&conn, "cheng-main", "cheng", Some("Cheng"))
        .unwrap()
        .unwrap();
    assert!(identity::set_web_participant_role(&conn, "cheng-main", "admin").unwrap());

    let (private_key, public_key) = signed_auth::generate_keypair();
    assert!(identity::set_web_participant_signing_key(&conn, "cheng-main", &public_key).unwrap());
    drop(conn);

    let request_target = "/api/admin/channels";
    let canonical = web_auth::canonical_http_request_bytes("cheng-main", "GET", request_target);
    let pkcs8 = URL_SAFE_NO_PAD
        .decode(
            private_key
                .strip_prefix(signed_auth::PRIVATE_KEY_PREFIX)
                .unwrap(),
        )
        .unwrap();
    let keypair = Ed25519KeyPair::from_pkcs8(&pkcs8).unwrap();
    let signature = URL_SAFE_NO_PAD.encode(keypair.sign(&canonical).as_ref());

    assert!(web_auth::verify_http_request_signature(
        &public_key,
        &signature,
        "cheng-main",
        "GET",
        request_target,
    ));

    let router = http::app(http::AppState {
        db_path,
        registration_key: None,
    });

    let agent_response = router
        .clone()
        .oneshot(
            Request::builder()
                .uri(request_target)
                .header(request_auth::PARTICIPANT_ID_HEADER, "cheng-main")
                .header(
                    request_auth::SIGNATURE_SCHEME_HEADER,
                    signed_auth::SIGNATURE_SCHEME,
                )
                .header(request_auth::SIGNATURE_HEADER, signature)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(agent_response.status(), StatusCode::UNAUTHORIZED);

    let human_session = web_auth::issue_web_session("cheng-main");
    let human_response = router
        .oneshot(
            Request::builder()
                .uri(request_target)
                .header(web_auth::WEB_SESSION_HEADER, human_session.token)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(human_response.status(), StatusCode::OK);
}

#[tokio::test]
async fn guest_session_is_forbidden_from_admin_control_plane() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let router = http::app(http::AppState {
        db_path,
        registration_key: None,
    });
    let guest = web_auth::issue_guest_session();

    let response = router
        .oneshot(
            Request::builder()
                .uri("/api/admin/channels")
                .header(web_auth::WEB_SESSION_HEADER, guest.token)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}
