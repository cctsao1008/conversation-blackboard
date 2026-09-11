from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"marker not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1))


old = r'''    let params = first_query_values(&uri);
    let private_key = params
        .get("key")
        .filter(|value| identity::validate_private_key(value))
        .cloned()
        .ok_or_else(ApiError::unauthorized)?;

    let lookup_participant = participant_id.clone();
    let identity = with_db(&state, move |conn| {
        identity::resolve_web_participant(conn, &lookup_participant, &private_key)
    })
    .await?
    .ok_or_else(ApiError::unauthorized)?;

    let channel = params
        .get("channel")
        .filter(|value| name_re().is_match(value))
        .cloned()
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_channel"))?;
    let kind = match params.get("kind") {
        None => "message".to_owned(),
        Some(value) if kind_re().is_match(value) => value.clone(),
        _ => return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_kind")),
    };
    let message_body = params
        .get("body")
        .filter(|value| !value.trim().is_empty())
        .cloned()
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_body"))?;
    if message_body.len() > MAX_BODY_BYTES {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "request_too_large",
        ));
    }
    let nonce = params
        .get("nonce")
        .filter(|value| nonce_re().is_match(value))
        .cloned()
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_nonce"))?;
    let reply_to = parse_reply_to(&params)?;
'''

new = r'''    let params = first_query_values(&uri);
    let channel = params
        .get("channel")
        .filter(|value| name_re().is_match(value))
        .cloned()
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_channel"))?;
    let kind = match params.get("kind") {
        None => "message".to_owned(),
        Some(value) if kind_re().is_match(value) => value.clone(),
        _ => return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_kind")),
    };
    let message_body = params
        .get("body")
        .filter(|value| !value.trim().is_empty())
        .cloned()
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_body"))?;
    if message_body.len() > MAX_BODY_BYTES {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "request_too_large",
        ));
    }
    let nonce = params
        .get("nonce")
        .filter(|value| nonce_re().is_match(value))
        .cloned()
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_nonce"))?;
    let reply_to = parse_reply_to(&params)?;

    let signed_attempt = params.contains_key("sig") || params.contains_key("scheme");
    let legacy_attempt = params.contains_key("key");
    if signed_attempt && legacy_attempt {
        return Err(ApiError::unauthorized());
    }

    let write_identity = if signed_attempt {
        let scheme = params
            .get("scheme")
            .filter(|value| value.as_str() == signed_auth::SIGNATURE_SCHEME)
            .ok_or_else(ApiError::unauthorized)?;
        let signature = params
            .get("sig")
            .filter(|value| !value.is_empty() && value.len() <= 256)
            .cloned()
            .ok_or_else(ApiError::unauthorized)?;
        let lookup_participant = participant_id.clone();
        let verify_channel = channel.clone();
        let verify_kind = kind.clone();
        let verify_body = message_body.clone();
        let verify_nonce = nonce.clone();
        let scheme = scheme.clone();
        with_db(&state, move |conn| {
            let Some(record) =
                identity::get_web_participant_verification(conn, &lookup_participant)?
            else {
                return Ok(None);
            };
            if record.signature_scheme != scheme
                || !signed_auth::verify_write_signature(
                    &record.public_key,
                    &signature,
                    &lookup_participant,
                    &verify_channel,
                    &verify_kind,
                    &verify_body,
                    reply_to,
                    &verify_nonce,
                )
            {
                return Ok(None);
            }
            Ok(Some(record.identity))
        })
        .await?
        .ok_or_else(ApiError::unauthorized)?
    } else {
        let private_key = params
            .get("key")
            .filter(|value| identity::validate_private_key(value))
            .cloned()
            .ok_or_else(ApiError::unauthorized)?;
        let lookup_participant = participant_id.clone();
        with_db(&state, move |conn| {
            identity::resolve_web_participant(conn, &lookup_participant, &private_key)
        })
        .await?
        .ok_or_else(ApiError::unauthorized)?
    };
'''

replace_once("src/http.rs", old, new)
replace_once(
    "src/http.rs",
    "    let write_identity = identity.clone();\n",
    "",
)

replace_once(
    "src/http_contract_tests.rs",
    "    identity,\n    model::Identity,\n",
    "    identity,\n    model::Identity,\n    signed_auth,\n",
)

append = r'''

#[tokio::test]
async fn navigation_write_accepts_ed25519_signature_without_raw_private_key() {
    let fixture = fixture("signed-nav");
    let (private_key, public_key) = signed_auth::generate_keypair();
    let conn = db::connect(&fixture.db_path).unwrap();
    assert!(identity::set_web_participant_signing_key(
        &conn,
        &fixture.participant_id,
        &public_key,
    )
    .unwrap());
    drop(conn);

    let signature = signed_auth::sign_write(
        &private_key,
        &fixture.participant_id,
        "conversation-architecture",
        "insight",
        "signed-hello",
        None,
        "signed-nav-001",
    )
    .unwrap();
    let uri = format!(
        "/w/{}?scheme={}&sig={}&channel=conversation-architecture&kind=insight&body=signed-hello&nonce=signed-nav-001",
        fixture.participant_id,
        signed_auth::SIGNATURE_SCHEME,
        signature,
    );

    let response = get(&fixture.router, &uri).await;
    let (status, _, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("status: created"));
    assert!(body.contains("source: signed-nav"));
    assert!(body.contains("participant_id: signed-nav-main"));

    let replay = get(&fixture.router, &uri).await;
    let (replay_status, _, replay_body) = response_text(replay).await;
    assert_eq!(replay_status, StatusCode::OK);
    assert!(replay_body.contains("status: existing"));
    assert!(replay_body.contains("idempotent: true"));

    let tampered = uri.replace("body=signed-hello", "body=tampered");
    assert_eq!(
        get(&fixture.router, &tampered).await.status(),
        StatusCode::UNAUTHORIZED
    );

    let ambiguous = format!("{uri}&key={}", fixture.private_key);
    assert_eq!(
        get(&fixture.router, &ambiguous).await.status(),
        StatusCode::UNAUTHORIZED
    );
}
'''

path = Path("src/http_contract_tests.rs")
text = path.read_text()
if "navigation_write_accepts_ed25519_signature_without_raw_private_key" not in text:
    path.write_text(text + append)
