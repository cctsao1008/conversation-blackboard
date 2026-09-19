from pathlib import Path

http = Path('src/http.rs')
text = http.read_text()

old = '''async fn channels(State(state): State<AppState>, headers: HeaderMap) -> Result<Response, ApiError> {\n    let access = require_read_access_for_target(&state, &headers, "GET", "/api/channels").await?;\n    let rows = match access {\n        ReadAccess::Guest => with_db(&state, db::list_public_channels).await?,\n        ReadAccess::Participant(identity) => {\n            let _ = identity.instance;\n            with_db(&state, db::list_channels).await?\n        }\n    };\n    Ok(json_response(StatusCode::OK, json!({"channels": rows})))\n}\n\nasync fn admin_channels(\n    State(state): State<AppState>,\n    headers: HeaderMap,\n) -> Result<Response, ApiError> {\n    let _admin = require_human_admin(&state, &headers).await?;\n    let rows = with_db(&state, db::list_channels).await?;\n    Ok(json_response(StatusCode::OK, json!({"channels": rows})))\n}\n'''
new = '''async fn channels(\n    State(state): State<AppState>,\n    headers: HeaderMap,\n    uri: Uri,\n) -> Result<Response, ApiError> {\n    let request_target = uri\n        .path_and_query()\n        .map(|value| value.as_str())\n        .unwrap_or_else(|| uri.path())\n        .to_owned();\n    let access =\n        require_read_access_for_target(&state, &headers, "GET", &request_target).await?;\n    let params = first_query_values(&uri);\n    let after_name = parse_channel_after_name(&params)?;\n    let limit = parse_channel_directory_limit(&params)?;\n    let public_only = matches!(access, ReadAccess::Guest);\n    if let ReadAccess::Participant(identity) = access {\n        let _ = identity.instance;\n    }\n    let window = with_db(&state, move |conn| {\n        db::read_channel_directory_window(\n            conn,\n            db::ChannelDirectoryWindowRequest {\n                after_name: after_name.as_deref(),\n                limit: Some(limit),\n                public_only,\n            },\n        )\n    })\n    .await?;\n    Ok(json_response(\n        StatusCode::OK,\n        json!({"channels": window.channels, "has_more": window.has_more}),\n    ))\n}\n\nasync fn admin_channels(\n    State(state): State<AppState>,\n    headers: HeaderMap,\n    uri: Uri,\n) -> Result<Response, ApiError> {\n    let _admin = require_human_admin(&state, &headers).await?;\n    let params = first_query_values(&uri);\n    let after_name = parse_channel_after_name(&params)?;\n    let limit = parse_channel_directory_limit(&params)?;\n    let window = with_db(&state, move |conn| {\n        db::read_channel_directory_window(\n            conn,\n            db::ChannelDirectoryWindowRequest {\n                after_name: after_name.as_deref(),\n                limit: Some(limit),\n                public_only: false,\n            },\n        )\n    })\n    .await?;\n    Ok(json_response(\n        StatusCode::OK,\n        json!({"channels": window.channels, "has_more": window.has_more}),\n    ))\n}\n'''
if old not in text:
    raise RuntimeError('HTTP channel handlers anchor not found')
text = text.replace(old, new, 1)

anchor = '''fn parse_reply_to(params: &HashMap<String, String>) -> Result<Option<i64>, ApiError> {\n'''
helpers = '''fn parse_channel_after_name(\n    params: &HashMap<String, String>,\n) -> Result<Option<String>, ApiError> {\n    match params.get("after_name") {\n        None => Ok(None),\n        Some(value) if name_re().is_match(value) => Ok(Some(value.clone())),\n        Some(_) => Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_query")),\n    }\n}\n\nfn parse_channel_directory_limit(params: &HashMap<String, String>) -> Result<usize, ApiError> {\n    let limit = params\n        .get("limit")\n        .map(String::as_str)\n        .unwrap_or("20")\n        .parse::<usize>()\n        .map_err(|_| ApiError::new(StatusCode::BAD_REQUEST, "invalid_query"))?;\n    if !(1..=db::MAX_CHANNEL_DIRECTORY_WINDOW_SIZE).contains(&limit) {\n        return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_query"));\n    }\n    Ok(limit)\n}\n\n'''
if anchor not in text:
    raise RuntimeError('HTTP parse helper anchor not found')
text = text.replace(anchor, helpers + anchor, 1)
http.write_text(text)

# Add focused HTTP pagination regression next to the existing guest channel contract.
tests = Path('src/http_contract_tests.rs')
t = tests.read_text()
anchor = '''#[tokio::test]\nasync fn human_admin_can_manage_channels_while_normal_human_cannot() {\n'''
case = r'''#[tokio::test]
async fn channel_directory_http_is_bounded_and_guest_cursor_stays_public() {
    let fixture = fixture("channel-window");
    let conn = db::connect(&fixture.db_path).unwrap();
    for index in 0..25 {
        let channel = format!("public-{index:02}");
        db::append_message(
            &conn,
            &fixture.identity,
            &channel,
            "message",
            "public",
            None,
        )
        .unwrap();
        db::update_channel(&conn, &channel, Some("public"), None).unwrap();
    }
    db::append_message(
        &conn,
        &fixture.identity,
        "private-hidden",
        "message",
        "private",
        None,
    )
    .unwrap();
    drop(conn);

    let guest = request(&fixture.router, Method::POST, "/api/auth/guest", None, None).await;
    let (_, guest_body) = response_json(guest).await;
    let token = guest_body["session_token"].as_str().unwrap();

    let first = request(
        &fixture.router,
        Method::GET,
        "/api/channels?limit=20",
        Some(token),
        None,
    )
    .await;
    let (status, first_body) = response_json(first).await;
    assert_eq!(status, StatusCode::OK);
    let first_rows = first_body["channels"].as_array().unwrap();
    assert_eq!(first_rows.len(), 20);
    assert_eq!(first_body["has_more"], true);
    assert!(first_rows.iter().all(|row| row["visibility"] == "public"));
    assert!(first_rows.iter().all(|row| row["status"] == "active"));
    let cursor = first_rows.last().unwrap()["channel"].as_str().unwrap();

    let second = request(
        &fixture.router,
        Method::GET,
        &format!("/api/channels?after_name={cursor}&limit=20"),
        Some(token),
        None,
    )
    .await;
    let (status, second_body) = response_json(second).await;
    assert_eq!(status, StatusCode::OK);
    let second_rows = second_body["channels"].as_array().unwrap();
    assert_eq!(second_rows.len(), 5);
    assert_eq!(second_body["has_more"], false);
    assert!(second_rows.iter().all(|row| row["channel"] != "private-hidden"));
    let mut traversed = first_rows
        .iter()
        .chain(second_rows.iter())
        .map(|row| row["channel"].as_str().unwrap().to_owned())
        .collect::<Vec<_>>();
    traversed.sort();
    traversed.dedup();
    assert_eq!(traversed.len(), 25);
    assert_eq!(traversed.first().map(String::as_str), Some("public-00"));
    assert_eq!(traversed.last().map(String::as_str), Some("public-24"));

    assert_eq!(
        request(
            &fixture.router,
            Method::GET,
            "/api/channels?limit=0",
            Some(token),
            None,
        )
        .await
        .status(),
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        request(
            &fixture.router,
            Method::GET,
            "/api/channels?after_name=%20bad",
            Some(token),
            None,
        )
        .await
        .status(),
        StatusCode::BAD_REQUEST
    );
}

'''
if anchor not in t:
    raise RuntimeError('HTTP test anchor not found')
t = t.replace(anchor, case + anchor, 1)
tests.write_text(t)

# Project the bounded contract into OpenAPI.
api = Path('integrations/openapi.yaml')
a = api.read_text()
old = '''      security:\n        - bearerAuth: []\n        - webSessionAuth: []\n      responses:\n        '200':\n          description: Channel summaries.\n          content:\n            application/json:\n              schema:\n                type: object\n                required: [channels]\n                properties:\n                  channels:\n                    type: array\n                    items:\n                      $ref: '#/components/schemas/ChannelSummary'\n        '401':\n          $ref: '#/components/responses/Unauthorized'\n\n  /api/messages:\n'''
new = '''      security:\n        - bearerAuth: []\n        - webSessionAuth: []\n      parameters:\n        - name: after_name\n          in: query\n          required: false\n          description: Exclusive channel-name cursor for stable ascending directory traversal.\n          schema:\n            type: string\n        - name: limit\n          in: query\n          required: false\n          schema:\n            type: integer\n            minimum: 1\n            maximum: 200\n            default: 20\n      responses:\n        '200':\n          description: Bounded channel directory window ordered by stable channel name.\n          content:\n            application/json:\n              schema:\n                type: object\n                required: [channels, has_more]\n                properties:\n                  channels:\n                    type: array\n                    items:\n                      $ref: '#/components/schemas/ChannelSummary'\n                  has_more:\n                    type: boolean\n        '400':\n          description: Invalid channel cursor or window limit.\n          content:\n            application/json:\n              schema:\n                $ref: '#/components/schemas/Error'\n        '401':\n          $ref: '#/components/responses/Unauthorized'\n\n  /api/messages:\n'''
if old not in a:
    raise RuntimeError('OpenAPI channel contract anchor not found')
a = a.replace(old, new, 1)
api.write_text(a)
