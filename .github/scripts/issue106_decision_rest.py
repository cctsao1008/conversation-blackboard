from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# Domain-safe target normalization and conservative external explanation projection.
p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")
old = '''use serde::Serialize;\n\nuse crate::execution::Principal;'''
new = '''use serde::Serialize;\n\nuse crate::{execution, execution::Principal, identity};'''
s = replace_once(s, old, new, "authorization imports")

marker = '''#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct AuthorizationIntegrityViolation {'''
insert = r'''#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorizationDecisionTarget {
    pub principal: Principal,
    pub participant_id: String,
    pub capability: String,
    pub resource: Option<String>,
    pub intent_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationDecisionExplanation {
    pub allowed: bool,
    pub source: &'static str,
    pub reason: &'static str,
    pub grant_id: Option<i64>,
    pub consume_on_commit: bool,
}

impl From<AuthorizationDecision> for AuthorizationDecisionExplanation {
    fn from(decision: AuthorizationDecision) -> Self {
        Self {
            allowed: decision.allowed,
            source: decision.source,
            reason: decision.reason,
            grant_id: decision.grant_id,
            consume_on_commit: decision.consume_grant_id.is_some(),
        }
    }
}

pub fn normalize_authorization_decision_target(
    principal_provider: &str,
    principal_subject: &str,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    intent_id: Option<&str>,
) -> Result<AuthorizationDecisionTarget, &'static str> {
    fn normalize(value: &str, max_bytes: usize) -> Option<String> {
        let value = value.trim();
        (!value.is_empty()
            && value.len() <= max_bytes
            && !value.chars().any(char::is_control))
        .then(|| value.to_owned())
    }

    let principal_provider = normalize(principal_provider, 256).ok_or("invalid_principal_provider")?;
    let principal_subject = normalize(principal_subject, 256).ok_or("invalid_principal_subject")?;
    let participant_id =
        identity::validate_participant_id(participant_id).ok_or("invalid_participant_id")?;
    let capability = normalize(capability, 128).ok_or("invalid_capability")?;
    if !is_known_capability(&capability) {
        return Err("unsupported_capability");
    }
    let resource = resource
        .map(|value| normalize(value, 256).ok_or("invalid_resource"))
        .transpose()?;
    let intent_id = intent_id
        .map(|value| execution::normalize_intent_id(value).map_err(|_| "invalid_intent_id"))
        .transpose()?;

    Ok(AuthorizationDecisionTarget {
        principal: Principal {
            provider: principal_provider,
            subject: principal_subject,
        },
        participant_id,
        capability,
        resource,
        intent_id,
    })
}

'''
if "pub struct AuthorizationDecisionExplanation" in s:
    raise SystemExit("decision explanation projection already exists")
s = replace_once(s, marker, insert + marker, "decision projection model")
p.write_text(s, encoding="utf-8")

# REST projection uses query-bound request auth and a physically read-only DB connection.
p = Path("src/access_api.rs")
s = p.read_text(encoding="utf-8")
old = '''    extract::{Path, State},\n    http::{header, HeaderMap, HeaderValue, StatusCode, Uri},\n    response::{IntoResponse, Response},\n    routing::get,\n    Json, Router,\n};\nuse serde_json::json;'''
new = '''    extract::{Path, Query, State},\n    http::{header, HeaderMap, HeaderValue, StatusCode, Uri},\n    response::{IntoResponse, Response},\n    routing::get,\n    Json, Router,\n};\nuse serde::Deserialize;\nuse serde_json::json;'''
s = replace_once(s, old, new, "access api imports")

old = '''        .route("/api/authorization-policy", get(authorization_policy))\n        .route(\n            "/api/authorization-policy/integrity",\n            get(authorization_policy_integrity),\n        )'''
new = '''        .route("/api/authorization-policy", get(authorization_policy))\n        .route(\n            "/api/authorization-policy/integrity",\n            get(authorization_policy_integrity),\n        )\n        .route(\n            "/api/authorization-decision/explain",\n            get(authorization_decision_explain),\n        )'''
s = replace_once(s, old, new, "decision explain route")

marker = '''async fn authorization_policy(\n'''
handler = r'''#[derive(Debug, Deserialize)]
struct AuthorizationDecisionExplainQuery {
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
    intent_id: Option<String>,
}

async fn authorization_decision_explain(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<AuthorizationDecisionExplainQuery>,
) -> Result<Response, AccessApiError> {
    // Authentication deliberately uses the full path_and_query target so the
    // participant-HMAC proof binds every decision-input query parameter.
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let caller_principal = principal_for_headers(&headers, &resolved);
    let target = authorization::normalize_authorization_decision_target(
        &query.principal_provider,
        &query.principal_subject,
        &query.participant_id,
        &query.capability,
        query.resource.as_deref(),
        query.intent_id.as_deref(),
    )
    .map_err(|code| AccessApiError::new(StatusCode::BAD_REQUEST, code))?;
    let caller_participant = resolved.instance.clone();

    let (schema_current, caller_allowed, decision) = with_db_read_only(&state, move |conn| {
        if !authorization::authorization_decision_schema_current(conn)? {
            return Ok((false, false, None));
        }
        let caller_decision = authorization::explain_authorization(
            conn,
            &caller_principal,
            &caller_participant,
            authorization::READ_AUTHORIZATION_DECISION,
            Some(authorization::AUTHORIZATION_DECISION_RESOURCE),
            None,
        )?;
        if !caller_decision.allowed {
            return Ok((true, false, None));
        }
        let decision = authorization::explain_authorization(
            conn,
            &target.principal,
            &target.participant_id,
            &target.capability,
            target.resource.as_deref(),
            target.intent_id.as_deref(),
        )?;
        Ok((
            true,
            true,
            Some(authorization::AuthorizationDecisionExplanation::from(decision)),
        ))
    })
    .await?;

    if !schema_current {
        return Err(AccessApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "authorization_schema_not_current",
        ));
    }
    if !caller_allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    Ok(json_response(
        StatusCode::OK,
        json!({"decision": decision.expect("authorized explain must return a decision")}),
    ))
}

'''
if "async fn authorization_decision_explain(" in s:
    raise SystemExit("REST decision handler already exists")
s = replace_once(s, marker, handler + marker, "decision handler")

marker = '''#[derive(Debug)]\nstruct AccessApiError {'''
helper = r'''async fn with_db_read_only<T, F>(state: &AppState, operation: F) -> Result<T, AccessApiError>
where
    T: Send + 'static,
    F: FnOnce(&rusqlite::Connection) -> rusqlite::Result<T> + Send + 'static,
{
    let path = state.db_path.clone();
    tokio::task::spawn_blocking(move || {
        let conn = crate::db::connect_read_only(&path)?;
        operation(&conn)
    })
    .await
    .map_err(|_| AccessApiError::new(StatusCode::INTERNAL_SERVER_ERROR, "internal_error"))?
    .map_err(|_| AccessApiError::new(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable"))
}

'''
if "async fn with_db_read_only" in s:
    raise SystemExit("read-only access helper already exists")
s = replace_once(s, marker, helper + marker, "read-only db helper")
p.write_text(s, encoding="utf-8")

# HTTP contract regressions.
p = Path("src/http_contract_tests.rs")
s = p.read_text(encoding="utf-8")
old = '''    identity,\n    model::Identity,\n    participant_auth, web_auth,\n};'''
new = '''    identity,\n    model::Identity,\n    participant_auth, request_auth, web_auth,\n};'''
s = replace_once(s, old, new, "HTTP test request_auth import")

marker = '''#[tokio::test]\nasync fn authorization_policy_integrity_http_is_privileged_read_only_and_reports_invalid_state() {'''
test = r'''#[tokio::test]
async fn authorization_decision_http_is_privileged_query_bound_and_read_only() {
    let fixture = fixture("decision-http");
    let router = fixture
        .router
        .clone()
        .merge(crate::access_api::app(AppState {
            db_path: fixture.db_path.clone(),
            registration_key: None,
        }));
    let target_query = "/api/authorization-decision/explain?principal_provider=oidc%3Ahttps%3A%2F%2Fissuer.example&principal_subject=target-agent&participant_id=decision-http-main&capability=post_message&resource=alpha";

    let unauthenticated = request(&router, Method::GET, target_query, None, None).await;
    assert_eq!(unauthenticated.status(), StatusCode::UNAUTHORIZED);

    let session = web_auth::issue_web_session(&fixture.participant_id);
    let ordinary = request(
        &router,
        Method::GET,
        target_query,
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(ordinary.status(), StatusCode::FORBIDDEN);

    // Snapshot authority is not decision-explain authority.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('human-web', ?1, ?1, 'read_authorization_policy', 'authorization-policy')",
        [&fixture.participant_id],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('oidc:https://issuer.example', 'target-agent', ?1, 'post_message', 'alpha')",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);
    let wrong_capability = request(
        &router,
        Method::GET,
        target_query,
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(wrong_capability.status(), StatusCode::FORBIDDEN);

    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "UPDATE web_participants SET role = 'admin' WHERE participant_id = ?1",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);

    // Caller is Human Web admin while the target is a distinct OIDC principal.
    let allowed = request(
        &router,
        Method::GET,
        target_query,
        Some(&session.token),
        None,
    )
    .await;
    let (allowed_status, allowed_body) = response_json(allowed).await;
    assert_eq!(allowed_status, StatusCode::OK);
    assert_eq!(allowed_body["decision"]["allowed"], true);
    assert_eq!(allowed_body["decision"]["source"], "principal_grants");
    assert_eq!(
        allowed_body["decision"]["reason"],
        "explicit_durable_grant_match"
    );
    assert_eq!(allowed_body["decision"]["consume_on_commit"], false);
    assert!(allowed_body["decision"].get("consume_grant_id").is_none());

    // A target denial is successful explanation data, not transport authorization failure.
    let denied_query = target_query.replace("resource=alpha", "resource=beta");
    let denied = request(
        &router,
        Method::GET,
        &denied_query,
        Some(&session.token),
        None,
    )
    .await;
    let (denied_status, denied_body) = response_json(denied).await;
    assert_eq!(denied_status, StatusCode::OK);
    assert_eq!(denied_body["decision"]["allowed"], false);
    assert_eq!(
        denied_body["decision"]["reason"],
        "explicit_resource_scope_mismatch"
    );

    // Explicit participant-HMAC explain authority succeeds, and its proof binds
    // the exact path+query target. Reusing the proof for a changed resource fails.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES ('participant-hmac', ?1, ?1, 'read_authorization_decision',
                 'authorization-decision')",
        [&fixture.participant_id],
    )
    .unwrap();
    drop(conn);
    let canonical = web_auth::canonical_http_request_bytes(
        &fixture.participant_id,
        "GET",
        target_query,
    );
    let proof = participant_auth::compute_message_proof(&fixture.signing_private, &canonical).unwrap();
    let hmac_request = |uri: &str| {
        Request::builder()
            .method(Method::GET)
            .uri(uri)
            .header(request_auth::PARTICIPANT_ID_HEADER, &fixture.participant_id)
            .header(request_auth::AUTH_SCHEME_HEADER, participant_auth::AUTH_SCHEME)
            .header(request_auth::AUTH_PROOF_HEADER, &proof)
            .body(Body::empty())
            .unwrap()
    };
    let hmac_allowed = router.clone().oneshot(hmac_request(target_query)).await.unwrap();
    assert_eq!(hmac_allowed.status(), StatusCode::OK);
    let changed_target = router
        .clone()
        .oneshot(hmac_request(&denied_query))
        .await
        .unwrap();
    assert_eq!(changed_target.status(), StatusCode::UNAUTHORIZED);

    // Invalid target inputs are rejected after caller authentication.
    let invalid = request(
        &router,
        Method::GET,
        "/api/authorization-decision/explain?principal_provider=oidc&principal_subject=target-agent&participant_id=decision-http-main&capability=definitely_unknown",
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(invalid.status(), StatusCode::BAD_REQUEST);

    // Legacy authorization state fails without recreating the missing table.
    let conn = db::connect(&fixture.db_path).unwrap();
    conn.execute("DROP TABLE delegated_grants", []).unwrap();
    drop(conn);
    let legacy = request(
        &router,
        Method::GET,
        target_query,
        Some(&session.token),
        None,
    )
    .await;
    assert_eq!(legacy.status(), StatusCode::SERVICE_UNAVAILABLE);
    let conn = db::connect(&fixture.db_path).unwrap();
    let table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'delegated_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(table_count, 0, "decision explain recreated authorization schema");
}

'''
if "authorization_decision_http_is_privileged_query_bound_and_read_only" in s:
    raise SystemExit("decision HTTP test already exists")
s = replace_once(s, marker, test + marker, "decision HTTP regression")
p.write_text(s, encoding="utf-8")
