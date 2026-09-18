from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    end_index = text.find(end, start_index + len(start))
    if start_index < 0 or end_index < 0:
        raise RuntimeError(f"{label}: boundary not found")
    return text[:start_index] + replacement + text[end_index:]


# The legacy full-snapshot contract is retained only as a test oracle.
path = Path('src/contract_schema.rs')
text = path.read_text()
old = 'pub fn authorization_policy_snapshot_schema() -> Value {'
new = '#[cfg(test)]\npub fn authorization_policy_snapshot_schema() -> Value {'
text = replace_once(text, old, new, 'test-only snapshot contract schema')
path.write_text(text)


# Canonical parity now targets the bounded selected-store window.
path = Path('src/contract_parity_tests.rs')
text = path.read_text()
start = '#[test]\nfn authorization_policy_snapshot_contracts_match_canonical_rust_shape() {'
end = '#[test]\nfn authorization_administration_openapi_matches_rest_only_phase1_contract() {'
replacement = '''#[test]
fn authorization_policy_window_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    let operation = &api["paths"]["/api/authorization-policy"]["get"];
    assert!(operation.is_object());
    assert_eq!(
        operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
        "#/components/schemas/AuthorizationPolicyEnvelope"
    );
    assert_eq!(
        names(&api, "/components/schemas/DurableGrantSnapshot/properties"),
        names(
            &contract_schema::durable_grant_snapshot_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/DelegatedGrantSnapshot/properties"
        ),
        names(
            &contract_schema::delegated_grant_snapshot_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationPolicyWindow/properties"
        ),
        names(
            &contract_schema::authorization_policy_window_schema(),
            "/properties"
        )
    );
    let parameter_names = operation["parameters"]
        .as_array()
        .unwrap()
        .iter()
        .map(|parameter| parameter["name"].as_str().unwrap().to_owned())
        .collect::<BTreeSet<_>>();
    assert_eq!(
        parameter_names,
        ["store", "order", "before", "after", "limit"]
            .into_iter()
            .map(str::to_owned)
            .collect()
    );
    let store = operation["parameters"]
        .as_array()
        .unwrap()
        .iter()
        .find(|parameter| parameter["name"] == "store")
        .unwrap();
    assert_eq!(store["required"], true);
    assert!(
        api["components"]["schemas"]["DelegatedGrantSnapshot"]["properties"]
            .get("created_at")
            .is_none()
    );
    assert!(
        api["components"]["schemas"]["DelegatedGrantSnapshot"]["properties"]
            .get("updated_at")
            .is_none()
    );

    let utcp = utcp_json();
    let policy = utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "authorization_policy")
        .expect("UTCP authorization policy window projection must exist");
    assert_eq!(
        names(policy, "/outputs/properties/policy/properties"),
        names(
            &contract_schema::authorization_policy_window_schema(),
            "/properties"
        )
    );
    let required_inputs = policy["inputs"]["required"]
        .as_array()
        .unwrap()
        .iter()
        .map(|value| value.as_str().unwrap().to_owned())
        .collect::<BTreeSet<_>>();
    assert_eq!(required_inputs, ["store"].into_iter().map(str::to_owned).collect());
    assert_eq!(
        names(
            policy,
            "/outputs/properties/policy/properties/durable_grants/items/properties"
        ),
        names(
            &contract_schema::durable_grant_snapshot_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            policy,
            "/outputs/properties/policy/properties/delegated_grants/items/properties"
        ),
        names(
            &contract_schema::delegated_grant_snapshot_schema(),
            "/properties"
        )
    );
    assert!(
        policy["outputs"]["properties"]["policy"]["properties"]["delegated_grants"]["items"]
            ["properties"]
            .get("created_at")
            .is_none()
    );
    assert_eq!(
        policy["tool_call_template"]["url"],
        "${BLACKBOARD_URL}/api/authorization-policy"
    );
    assert_eq!(policy["tool_call_template"]["http_method"], "GET");
}

'''
text = replace_between(text, start, end, replacement, 'policy window parity contract')
old = '''#[test]
fn authorization_policy_snapshot_adapters_reuse_canonical_reader_without_grant_sql() {
    let adapters = [
        ("http", include_str!("access_api.rs")),
        ("mcp", include_str!("mcp.rs")),
    ];
    for (name, source) in adapters {
        assert!(
            source.contains("read_authorization_policy_snapshot"),
            "{name} adapter must reuse the canonical authorization snapshot reader"
        );
        for forbidden in [
            "FROM principal_grants",
            "FROM delegated_grants",
            "JOIN principal_grants",
            "JOIN delegated_grants",
        ] {
            assert!(
                !source.contains(forbidden),
                "{name} adapter must not enumerate grant tables directly: {forbidden}"
            );
        }
    }
}'''
new = '''#[test]
fn authorization_policy_window_adapters_reuse_canonical_reader_without_grant_sql() {
    let adapters = [
        ("http", include_str!("access_api.rs")),
        ("mcp", include_str!("mcp.rs")),
    ];
    for (name, source) in adapters {
        assert!(
            source.contains("read_authorization_policy_window"),
            "{name} adapter must reuse the canonical bounded authorization policy reader"
        );
        assert!(
            !source.contains("read_authorization_policy_snapshot"),
            "{name} adapter must not reach the full authorization policy snapshot helper"
        );
        for forbidden in [
            "FROM principal_grants",
            "FROM delegated_grants",
            "JOIN principal_grants",
            "JOIN delegated_grants",
        ] {
            assert!(
                !source.contains(forbidden),
                "{name} adapter must not enumerate grant tables directly: {forbidden}"
            );
        }
    }
}'''
text = replace_once(text, old, new, 'policy window adapter source guard')
path.write_text(text)


# REST acceptance: explicit store, bounded pages, independent ID domains, lifecycle visibility.
path = Path('src/http_contract_tests.rs')
text = path.read_text()
start = '#[tokio::test]\nasync fn authorization_policy_http_is_privileged_canonical_and_observationally_read_only() {'
end = '#[tokio::test]\nasync fn authorization_decision_http_is_privileged_query_bound_and_read_only() {'
replacement = '''#[tokio::test]
async fn authorization_policy_http_is_privileged_bounded_and_observationally_read_only() {
    let fixture = fixture("policy-window");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let durable_uri = "/api/authorization-policy?store=durable&limit=200";

    let unauthenticated = request(&router, Method::GET, durable_uri, None, None).await;
    assert_eq!(unauthenticated.status(), StatusCode::UNAUTHORIZED);

    let session = web_auth::issue_web_session(&fixture.participant_id);
    let ordinary = request(
        &router,
        Method::GET,
        durable_uri,
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    // Policy-integrity authority is deliberately separate from policy inventory.
    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('human-web', ?1, ?1, 'read_authorization_policy_integrity',
                 'authorization-policy-integrity')",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);
    let wrong_capability = request(
        &router,
        Method::GET,
        durable_uri,
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(wrong_capability.status(), StatusCode::FORBIDDEN);

    // Seed lifecycle state that must remain visible inside its selected store.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource, status)
         VALUES ('oidc:https://issuer.example', 'active-agent', ?1,
                 'post_message', 'alpha', 'active')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource, status)
         VALUES ('oidc:https://issuer.example', 'inactive-agent', ?1,
                 'read_messages', NULL, 'inactive')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource,
             intent_id, expires_at, one_shot, status)
         VALUES ('oidc:https://issuer.example', 'expired-agent', ?1, 'post_message',
                 'beta', 'expired-http-intent', unixepoch() - 60, 0, 'active')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource,
             intent_id, one_shot, consumed_at, consumed_intent_id, status)
         VALUES ('oidc:https://issuer.example', 'consumed-agent', ?1, 'post_message',
                 'gamma', 'consumed-http-intent', 1, unixepoch(),
                 'consumed-http-intent', 'inactive')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "UPDATE web_participants SET role = 'admin' WHERE participant_id = ?1",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);

    let response = request(
        &router,
        Method::GET,
        durable_uri,
        Some(&session.token),
        None,
    )
    .await;
    let (status, durable_body) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(durable_body["policy"]["store"], "durable");
    assert_eq!(durable_body["policy"]["order"], "desc");
    assert!(durable_body["policy"]["delegated_grants"]
        .as_array()
        .unwrap()
        .is_empty());
    let durable = durable_body["policy"]["durable_grants"].as_array().unwrap();
    assert!(durable.iter().any(|entry| {
        entry["principal_subject"] == "active-agent" && entry["status"] == "active"
    }));
    assert!(durable.iter().any(|entry| {
        entry["principal_subject"] == "inactive-agent" && entry["status"] == "inactive"
    }));
    assert!(durable
        .iter()
        .all(|entry| entry.get("created_at").is_some() && entry.get("updated_at").is_some()));

    let delegated_response = request(
        &router,
        Method::GET,
        "/api/authorization-policy?store=delegated&order=asc&limit=200",
        Some(&session.token),
        None,
    )
    .await;
    let (status, delegated_body) = response_json(delegated_response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(delegated_body["policy"]["store"], "delegated");
    assert_eq!(delegated_body["policy"]["order"], "asc");
    assert!(delegated_body["policy"]["durable_grants"]
        .as_array()
        .unwrap()
        .is_empty());
    let delegated = delegated_body["policy"]["delegated_grants"]
        .as_array()
        .unwrap();
    assert!(delegated.iter().any(|entry| {
        entry["intent_id"] == "expired-http-intent" && entry["expires_at"].is_number()
    }));
    assert!(delegated.iter().any(|entry| {
        entry["intent_id"] == "consumed-http-intent"
            && entry["one_shot"] == true
            && entry["consumed_at"].is_number()
            && entry["consumed_intent_id"] == "consumed-http-intent"
            && entry["status"] == "inactive"
    }));
    assert!(delegated
        .iter()
        .all(|entry| entry.get("created_at").is_none() && entry.get("updated_at").is_none()));

    // A one-row window must expose another page, and the exclusive cursor must not overlap.
    let first = request(
        &router,
        Method::GET,
        "/api/authorization-policy?store=durable&limit=1",
        Some(&session.token),
        None,
    )
    .await;
    let (status, first_body) = response_json(first).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(first_body["policy"]["has_more"], true);
    let first_id = first_body["policy"]["durable_grants"][0]["id"]
        .as_i64()
        .unwrap();
    let second_uri = format!(
        "/api/authorization-policy?store=durable&limit=1&before={first_id}"
    );
    let second = request(
        &router,
        Method::GET,
        &second_uri,
        Some(&session.token),
        None,
    )
    .await;
    let (status, second_body) = response_json(second).await;
    assert_eq!(status, StatusCode::OK);
    let second_id = second_body["policy"]["durable_grants"][0]["id"]
        .as_i64()
        .unwrap();
    assert!(second_id < first_id);
    assert_ne!(second_id, first_id);

    for invalid_uri in [
        "/api/authorization-policy",
        "/api/authorization-policy?store=bogus",
        "/api/authorization-policy?store=durable&before=2&after=1",
        "/api/authorization-policy?store=durable&order=asc&before=2",
        "/api/authorization-policy?store=durable&limit=201",
    ] {
        let invalid = request(
            &router,
            Method::GET,
            invalid_uri,
            Some(&session.token),
            None,
        )
        .await;
        assert_eq!(invalid.status(), StatusCode::BAD_REQUEST, "{invalid_uri}");
    }

    let serialized = format!("{durable_body}{delegated_body}");
    assert!(!serialized.contains(&fixture.signing_private));

    // Legacy schema must fail before authorize() compatibility logic can mutate it.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    drop(conn);
    let legacy = request(
        &router,
        Method::GET,
        "/api/authorization-policy?store=durable",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(legacy.status(), StatusCode::SERVICE_UNAVAILABLE);
    let conn = db::connect(&fixture.db_path).unwrap();
    let after: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'delegated_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(after, 0, "read-only policy window recreated authorization schema");
}

'''
text = replace_between(text, start, end, replacement, 'REST bounded policy acceptance')
path.write_text(text)


# MCP helper and discovery acceptance now make store explicit.
path = Path('src/mcp_contract_tests.rs')
text = path.read_text()
old = '''fn policy_snapshot_arguments(secret: &str, participant_id: &str) -> Value {
    let proof = participant_auth::compute_capability_proof(
        secret,
        participant_id,
        authorization::READ_AUTHORIZATION_POLICY,
        Some(authorization::AUTHORIZATION_POLICY_RESOURCE),
    )
    .unwrap();
    json!({
        "participant_id": participant_id,
        "auth": {"scheme": participant_auth::AUTH_SCHEME, "proof": proof}
    })
}'''
new = '''fn policy_window_arguments(secret: &str, participant_id: &str, store: &str) -> Value {
    let proof = participant_auth::compute_capability_proof(
        secret,
        participant_id,
        authorization::READ_AUTHORIZATION_POLICY,
        Some(authorization::AUTHORIZATION_POLICY_RESOURCE),
    )
    .unwrap();
    json!({
        "participant_id": participant_id,
        "store": store,
        "auth": {"scheme": participant_auth::AUTH_SCHEME, "proof": proof}
    })
}'''
text = replace_once(text, old, new, 'MCP policy window arguments helper')
start = '#[tokio::test]\nasync fn mcp_policy_snapshot_tool_uses_canonical_read_only_schema() {'
end = '#[tokio::test]\nasync fn mcp_read_schema_includes_conversation_ref_provenance() {'
replacement = '''#[tokio::test]
async fn mcp_policy_window_tool_uses_canonical_read_only_schema() {
    let fixture = fixture();
    let response = request(
        &fixture.router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 122,
            "method": "tools/list",
            "params": {}
        })),
    )
    .await;
    let (_, value) = response_json(response).await;
    let tool = value["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "blackboard_authorization_policy")
        .expect("policy window tool must be advertised");
    assert_eq!(
        tool["outputSchema"],
        contract_schema::authorization_policy_envelope_schema()
    );
    assert_eq!(tool["annotations"]["readOnlyHint"], true);
    let required = tool["inputSchema"]["required"].as_array().unwrap();
    assert!(required.iter().any(|field| field == "participant_id"));
    assert!(required.iter().any(|field| field == "store"));
    assert!(required.iter().any(|field| field == "auth"));
    for field in ["before", "after", "limit", "order"] {
        assert!(tool["inputSchema"]["properties"].get(field).is_some());
    }
    assert_eq!(
        tool["outputSchema"]["properties"]["policy"]["properties"]["store"]["enum"][0],
        "durable"
    );
    assert!(tool["outputSchema"]["properties"]["policy"]["properties"]
        .get("has_more")
        .is_some());
    let delegated_properties = &tool["outputSchema"]["properties"]["policy"]["properties"]
        ["delegated_grants"]["items"]["properties"];
    assert!(delegated_properties.get("created_at").is_none());
    assert!(delegated_properties.get("updated_at").is_none());
}

'''
text = replace_between(text, start, end, replacement, 'MCP policy discovery acceptance')
start = '#[tokio::test]\nasync fn mcp_policy_snapshot_requires_its_own_explicit_hmac_grant_and_never_migrates_schema() {'
end = '#[tokio::test]\nasync fn bearer_policy_snapshot_requires_explicit_blackboard_grant() {'
replacement = '''#[tokio::test]
async fn mcp_policy_window_requires_its_own_explicit_hmac_grant_and_never_migrates_schema() {
    let fixture = fixture();

    let denied = call_tool(
        &fixture.router,
        123,
        "blackboard_authorization_policy",
        policy_window_arguments(&fixture.single_secret, "single-main", "durable"),
    )
    .await;
    assert_eq!(tool_error_code(&denied), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main',
                 'read_authorization_policy_integrity', 'authorization-policy-integrity')",
        [],
    )
    .unwrap();
    drop(conn);

    let wrong_capability = call_tool(
        &fixture.router,
        124,
        "blackboard_authorization_policy",
        policy_window_arguments(&fixture.single_secret, "single-main", "durable"),
    )
    .await;
    assert_eq!(tool_error_code(&wrong_capability), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', 'single-main', 'single-main',
                 'read_authorization_policy', 'authorization-policy')",
        [],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource,
             intent_id, expires_at, one_shot, status)
         VALUES ('oidc:https://issuer.example', 'expired-agent', 'single-main', 'post_message',
                 'alpha', 'window-expired-001', unixepoch() - 60, 0, 'active')",
        [],
    )
    .unwrap();
    drop(conn);

    let durable = call_tool(
        &fixture.router,
        125,
        "blackboard_authorization_policy",
        policy_window_arguments(&fixture.single_secret, "single-main", "durable"),
    )
    .await;
    assert_eq!(durable["result"]["isError"], false);
    assert_eq!(
        durable["result"]["structuredContent"]["policy"]["store"],
        "durable"
    );
    assert!(durable["result"]["structuredContent"]["policy"]["delegated_grants"]
        .as_array()
        .unwrap()
        .is_empty());
    assert!(durable["result"]["structuredContent"]["policy"]["durable_grants"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| {
            entry["capability"] == authorization::READ_AUTHORIZATION_POLICY
                && entry["resource"] == authorization::AUTHORIZATION_POLICY_RESOURCE
        }));

    let delegated = call_tool(
        &fixture.router,
        1251,
        "blackboard_authorization_policy",
        policy_window_arguments(&fixture.single_secret, "single-main", "delegated"),
    )
    .await;
    assert_eq!(delegated["result"]["isError"], false);
    assert_eq!(
        delegated["result"]["structuredContent"]["policy"]["store"],
        "delegated"
    );
    assert!(delegated["result"]["structuredContent"]["policy"]["durable_grants"]
        .as_array()
        .unwrap()
        .is_empty());
    assert!(delegated["result"]["structuredContent"]["policy"]["delegated_grants"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| {
            entry["intent_id"] == "window-expired-001" && entry["expires_at"].is_number()
        }));

    let mut first_args = policy_window_arguments(&fixture.single_secret, "single-main", "durable");
    first_args["limit"] = json!(1);
    let first = call_tool(
        &fixture.router,
        1252,
        "blackboard_authorization_policy",
        first_args,
    )
    .await;
    assert_eq!(first["result"]["isError"], false);
    assert_eq!(
        first["result"]["structuredContent"]["policy"]["has_more"],
        true
    );
    let first_id = first["result"]["structuredContent"]["policy"]["durable_grants"][0]["id"]
        .as_i64()
        .unwrap();
    let mut second_args = policy_window_arguments(&fixture.single_secret, "single-main", "durable");
    second_args["limit"] = json!(1);
    second_args["before"] = json!(first_id);
    let second = call_tool(
        &fixture.router,
        1253,
        "blackboard_authorization_policy",
        second_args,
    )
    .await;
    assert_eq!(second["result"]["isError"], false);
    let second_id = second["result"]["structuredContent"]["policy"]["durable_grants"][0]["id"]
        .as_i64()
        .unwrap();
    assert!(second_id < first_id);
    assert_ne!(second_id, first_id);

    // Policy-window authority alone does not imply policy-integrity authority.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "UPDATE principal_grants SET status = 'inactive'
         WHERE principal_provider = 'participant-hmac'
           AND principal_subject = 'single-main'
           AND participant_id = 'single-main'
           AND capability = 'read_authorization_policy_integrity'",
        [],
    )
    .unwrap();
    drop(conn);
    let integrity = call_tool(
        &fixture.router,
        126,
        "blackboard_authorization_policy_integrity",
        policy_integrity_arguments(&fixture.single_secret, "single-main"),
    )
    .await;
    assert_eq!(tool_error_code(&integrity), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    drop(conn);

    let legacy = call_tool(
        &fixture.router,
        127,
        "blackboard_authorization_policy",
        policy_window_arguments(&fixture.single_secret, "single-main", "durable"),
    )
    .await;
    assert_eq!(tool_error_code(&legacy), "authorization_schema_not_current");
    let conn = db::connect(&fixture.db_path).unwrap();
    let table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'delegated_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(table_count, 0, "MCP policy window read recreated authorization schema");
}

'''
text = replace_between(text, start, end, replacement, 'MCP HMAC bounded policy acceptance')
start = '#[tokio::test]\nasync fn bearer_policy_snapshot_requires_explicit_blackboard_grant() {'
end = '#[tokio::test]\nasync fn mcp_policy_integrity_requires_its_own_explicit_hmac_grant_and_never_migrates_schema() {'
replacement = '''#[tokio::test]
async fn bearer_policy_window_requires_explicit_blackboard_grant() {
    let fixture = fixture();
    let (verifier, token) = bearer_verifier_and_token("policy-reader");
    let router = mcp::app_with_oidc(
        AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        },
        Some(Arc::new(verifier)),
    );
    let authorization_header = format!("Bearer {token}");

    let denied = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 128,
            "method": "tools/call",
            "params": {
                "name": "blackboard_authorization_policy",
                "arguments": {"participant_id": "single-main", "store": "durable"}
            }
        }),
        &authorization_header,
    )
    .await;
    let (status, denied_value) = response_json(denied).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(tool_error_code(&denied_value), "forbidden");

    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('oidc:https://issuer.example', 'policy-reader', 'single-main',
                 'read_authorization_policy', 'authorization-policy')",
        [],
    )
    .unwrap();
    drop(conn);

    let allowed = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 129,
            "method": "tools/call",
            "params": {
                "name": "blackboard_authorization_policy",
                "arguments": {"participant_id": "single-main", "store": "durable"}
            }
        }),
        &authorization_header,
    )
    .await;
    let (status, allowed_value) = response_json(allowed).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(allowed_value["result"]["isError"], false);
    assert_eq!(
        allowed_value["result"]["structuredContent"]["policy"]["store"],
        "durable"
    );
    assert!(
        allowed_value["result"]["structuredContent"]["policy"]["durable_grants"]
            .as_array()
            .unwrap()
            .iter()
            .any(|entry| entry["principal_subject"] == "policy-reader")
    );
    assert!(allowed_value["result"]["structuredContent"]["policy"]["delegated_grants"]
        .as_array()
        .unwrap()
        .is_empty());

    // A verified Bearer must not make a stale authorization schema writable.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    drop(conn);
    let legacy = request_with_authorization(
        &router,
        json!({
            "jsonrpc": "2.0",
            "id": 130,
            "method": "tools/call",
            "params": {
                "name": "blackboard_authorization_policy",
                "arguments": {"participant_id": "single-main", "store": "durable"}
            }
        }),
        &authorization_header,
    )
    .await;
    let (status, legacy_value) = response_json(legacy).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        tool_error_code(&legacy_value),
        "authorization_schema_not_current"
    );
    let conn = db::connect(&fixture.db_path).unwrap();
    let table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'delegated_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(table_count, 0, "Bearer policy window read recreated authorization schema");
}

'''
text = replace_between(text, start, end, replacement, 'MCP Bearer bounded policy acceptance')
path.write_text(text)
