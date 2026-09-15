use axum::{
    extract::{Path, Query, State},
    http::{header, HeaderMap, HeaderValue, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{delete, get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::json;

use crate::{
    authorization, authorization_admin, execution, http::AppState, identity, model::Identity,
    request_auth, web_auth,
};

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/api/access-context", get(access_context))
        .route("/api/executions/{intent_id}", get(execution_receipt))
        .route("/api/executions/{intent_id}/audit", get(execution_audit))
        .route(
            "/api/executions/{intent_id}/audit/integrity",
            get(execution_audit_integrity),
        )
        .route("/api/execution-audit/sweep", get(execution_audit_sweep))
        .route("/api/authorization-policy", get(authorization_policy))
        .route(
            "/api/authorization-policy/integrity",
            get(authorization_policy_integrity),
        )
        .route(
            "/api/authorization-decision/explain",
            get(authorization_decision_explain),
        )
        .route(
            "/api/authorization-grants/durable",
            post(create_durable_authorization_grant),
        )
        .route(
            "/api/authorization-grants/durable/{grant_id}",
            delete(deactivate_durable_authorization_grant),
        )
        .with_state(state)
}

#[derive(Debug, Deserialize)]
struct DurableGrantCreateBody {
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
}

async fn create_durable_authorization_grant(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<DurableGrantCreateBody>,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "POST", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    if principal.provider == "participant-hmac" {
        return Err(AccessApiError::new(
            StatusCode::FORBIDDEN,
            "unsupported_authentication",
        ));
    }

    let caller_participant_id = resolved.instance.clone();
    let outcome = with_db_access(&state, move |conn| {
        let request = authorization_admin::DurableGrantCreateRequest {
            principal_provider: &body.principal_provider,
            principal_subject: &body.principal_subject,
            participant_id: &body.participant_id,
            capability: &body.capability,
            resource: body.resource.as_deref(),
        };
        authorization_admin::create_durable_grant_authorized(
            conn,
            "rest-authorization-admin",
            &principal,
            &caller_participant_id,
            &request,
        )
        .map_err(map_administration_error)
    })
    .await?;

    let state_name = match outcome.state {
        authorization_admin::DurableGrantCreateState::Created => "created",
        authorization_admin::DurableGrantCreateState::Existing => "existing",
        authorization_admin::DurableGrantCreateState::Reactivated => "reactivated",
    };
    let status = if outcome.state == authorization_admin::DurableGrantCreateState::Created {
        StatusCode::CREATED
    } else {
        StatusCode::OK
    };
    Ok(json_response(
        status,
        json!({
            "grant": {
                "store": "durable",
                "id": outcome.id,
                "state": state_name,
            }
        }),
    ))
}

async fn deactivate_durable_authorization_grant(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(grant_id): Path<i64>,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "DELETE", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    if principal.provider == "participant-hmac" {
        return Err(AccessApiError::new(
            StatusCode::FORBIDDEN,
            "unsupported_authentication",
        ));
    }

    let caller_participant_id = resolved.instance.clone();
    let outcome = with_db_access(&state, move |conn| {
        authorization_admin::deactivate_durable_grant_authorized(
            conn,
            "rest-authorization-admin",
            &principal,
            &caller_participant_id,
            grant_id,
        )
        .map_err(map_administration_error)
    })
    .await?
    .ok_or_else(|| AccessApiError::new(StatusCode::NOT_FOUND, "grant_not_found"))?;

    Ok(json_response(
        StatusCode::OK,
        json!({
            "grant": {
                "store": "durable",
                "id": grant_id,
                "state": "inactive",
                "rows_changed": outcome.rows_changed,
            }
        }),
    ))
}

fn map_administration_error(error: Box<dyn std::error::Error + Send + Sync>) -> AccessApiError {
    let message = error.to_string();
    if message == "authorization denied" {
        return AccessApiError::new(StatusCode::FORBIDDEN, "forbidden");
    }
    if message.contains("schema requires migration") {
        return AccessApiError::new(StatusCode::SERVICE_UNAVAILABLE, "schema_migration_required");
    }
    if error.downcast_ref::<rusqlite::Error>().is_some() {
        return AccessApiError::new(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable");
    }
    AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_grant_request")
}

async fn access_context(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let lookup = resolved.instance.clone();
    let principal_for_grants = principal.clone();
    let (role, grants) = with_db(&state, move |conn| {
        let role = identity::get_web_participant_role(conn, &lookup)?;
        let grants = if role.is_some() {
            authorization::effective_grants(conn, &principal_for_grants, &lookup)?
        } else {
            Vec::new()
        };
        Ok((role, grants))
    })
    .await?;

    let participant_id = role.as_ref().map(|_| resolved.instance.clone());
    let mut capabilities = grants
        .iter()
        .map(|grant| grant.capability.clone())
        .collect::<Vec<_>>();
    capabilities.sort();
    capabilities.dedup();

    Ok(json_response(
        StatusCode::OK,
        json!({
            "identity": {
                "source": resolved.source,
                "instance": resolved.instance,
                "label": resolved.label,
            },
            "principal": principal,
            "participant_id": participant_id,
            "role": role.unwrap_or_else(|| "user".to_owned()),
            "capabilities": capabilities,
            "grants": grants,
        }),
    ))
}

async fn execution_receipt(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(intent_id): Path<String>,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let intent_id = execution::normalize_intent_id(&intent_id)
        .map_err(|_| AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_intent_id"))?;
    let participant_id = resolved.instance.clone();
    let receipt = with_db(&state, move |conn| {
        execution::get_execution_receipt(conn, &participant_id, &intent_id)
    })
    .await?
    .ok_or_else(|| AccessApiError::new(StatusCode::NOT_FOUND, "execution_not_found"))?;

    Ok(json_response(StatusCode::OK, json!({"execution": receipt})))
}

async fn execution_audit(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(intent_id): Path<String>,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let intent_id = execution::normalize_intent_id(&intent_id)
        .map_err(|_| AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_intent_id"))?;
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let (allowed, audit) = with_db(&state, move |conn| {
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )?;
        let audit = if allowed {
            execution::get_execution_audit_bundle(conn, &lookup_participant, &lookup_intent)?
        } else {
            None
        };
        Ok((allowed, audit))
    })
    .await?;
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    let audit =
        audit.ok_or_else(|| AccessApiError::new(StatusCode::NOT_FOUND, "execution_not_found"))?;
    Ok(json_response(StatusCode::OK, json!({"audit": audit})))
}

async fn execution_audit_integrity(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Path(intent_id): Path<String>,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let intent_id = execution::normalize_intent_id(&intent_id)
        .map_err(|_| AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_intent_id"))?;
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let (allowed, report) = with_db(&state, move |conn| {
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )?;
        let report = if allowed {
            Some(execution::verify_execution_audit_integrity(
                conn,
                &lookup_participant,
                &lookup_intent,
            )?)
        } else {
            None
        };
        Ok((allowed, report))
    })
    .await?;
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    let report = report.expect("authorized integrity verification must produce a report");
    Ok(json_response(StatusCode::OK, json!({"integrity": report})))
}

async fn execution_audit_sweep(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (allowed, report) = with_db(&state, move |conn| {
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT_SWEEP,
            Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
        )?;
        let report = if allowed {
            Some(execution::sweep_execution_audit_integrity(conn)?)
        } else {
            None
        };
        Ok((allowed, report))
    })
    .await?;
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    let report = report.expect("authorized audit sweep must produce a report");
    Ok(json_response(StatusCode::OK, json!({"sweep": report})))
}

#[derive(Debug, Deserialize)]
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
            Some(authorization::AuthorizationDecisionExplanation::from(
                decision,
            )),
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

async fn authorization_policy(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (allowed, schema_current, snapshot) = with_db(&state, move |conn| {
        let schema_current = authorization::authorization_policy_snapshot_schema_current(conn)?;
        if !schema_current {
            return Ok((false, false, None));
        }
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY,
            Some(authorization::AUTHORIZATION_POLICY_RESOURCE),
        )?;
        if !allowed {
            return Ok((false, true, None));
        }
        Ok((
            true,
            true,
            Some(authorization::read_authorization_policy_snapshot(conn)?),
        ))
    })
    .await?;
    if !schema_current {
        return Err(AccessApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "authorization_schema_not_current",
        ));
    }
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    let snapshot = snapshot.expect("authorized current-schema policy read must produce snapshot");
    Ok(json_response(StatusCode::OK, json!({"policy": snapshot})))
}

async fn authorization_policy_integrity(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, AccessApiError> {
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let participant_id = resolved.instance.clone();
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (allowed, schema_current, report) = with_db(&state, move |conn| {
        let schema_current = authorization::authorization_integrity_schema_current(conn)?;
        if !schema_current {
            return Ok((false, false, None));
        }
        let allowed = authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        )?;
        if !allowed {
            return Ok((false, true, None));
        }
        Ok((
            true,
            true,
            Some(authorization::audit_authorization_integrity(conn)?),
        ))
    })
    .await?;
    if !schema_current {
        return Err(AccessApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "authorization_schema_not_current",
        ));
    }
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    let report =
        report.expect("authorized current-schema policy integrity read must produce report");
    Ok(json_response(StatusCode::OK, json!({"integrity": report})))
}

fn principal_for_headers(headers: &HeaderMap, resolved: &Identity) -> execution::Principal {
    let provider = if headers.contains_key(web_auth::WEB_SESSION_HEADER) {
        "human-web"
    } else if headers.contains_key(request_auth::AUTH_PROOF_HEADER)
        || headers.contains_key(request_auth::AUTH_SCHEME_HEADER)
    {
        "participant-hmac"
    } else {
        "bearer"
    };
    execution::Principal {
        provider: provider.to_owned(),
        subject: resolved.instance.clone(),
    }
}

async fn require_identity_for_target(
    state: &AppState,
    headers: &HeaderMap,
    method: &'static str,
    uri: &Uri,
) -> Result<Identity, AccessApiError> {
    let headers = headers.clone();
    let request_target = uri
        .path_and_query()
        .map(|value| value.as_str())
        .unwrap_or_else(|| uri.path())
        .to_owned();
    with_db(state, move |conn| {
        request_auth::resolve_request_identity_for_target(conn, &headers, method, &request_target)
    })
    .await?
    .ok_or_else(AccessApiError::unauthorized)
}

async fn with_db<T, F>(state: &AppState, operation: F) -> Result<T, AccessApiError>
where
    T: Send + 'static,
    F: FnOnce(&rusqlite::Connection) -> rusqlite::Result<T> + Send + 'static,
{
    let path = state.db_path.clone();
    tokio::task::spawn_blocking(move || {
        let conn = crate::db::connect(&path)?;
        operation(&conn)
    })
    .await
    .map_err(|_| AccessApiError::new(StatusCode::INTERNAL_SERVER_ERROR, "internal_error"))?
    .map_err(|_| AccessApiError::new(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable"))
}

async fn with_db_access<T, F>(state: &AppState, operation: F) -> Result<T, AccessApiError>
where
    T: Send + 'static,
    F: FnOnce(&rusqlite::Connection) -> Result<T, AccessApiError> + Send + 'static,
{
    let path = state.db_path.clone();
    tokio::task::spawn_blocking(move || {
        let conn = crate::db::connect(&path).map_err(|_| {
            AccessApiError::new(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable")
        })?;
        operation(&conn)
    })
    .await
    .map_err(|_| AccessApiError::new(StatusCode::INTERNAL_SERVER_ERROR, "internal_error"))?
}

async fn with_db_read_only<T, F>(state: &AppState, operation: F) -> Result<T, AccessApiError>
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

#[derive(Debug)]
struct AccessApiError {
    status: StatusCode,
    code: &'static str,
}

impl AccessApiError {
    fn new(status: StatusCode, code: &'static str) -> Self {
        Self { status, code }
    }

    fn unauthorized() -> Self {
        Self::new(StatusCode::UNAUTHORIZED, "unauthorized")
    }
}

impl IntoResponse for AccessApiError {
    fn into_response(self) -> Response {
        json_response(self.status, json!({"error": self.code}))
    }
}

fn json_response(status: StatusCode, value: serde_json::Value) -> Response {
    let mut response = (status, Json(value)).into_response();
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}
