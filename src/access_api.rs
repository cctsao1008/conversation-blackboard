use axum::{
    extract::{Path, State},
    http::{header, HeaderMap, HeaderValue, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use rusqlite::OptionalExtension;
use serde_json::json;

use crate::{
    authorization, execution, http::AppState, identity, model::Identity, request_auth, web_auth,
};

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/api/access-context", get(access_context))
        .route("/api/executions/{intent_id}", get(execution_receipt))
        .with_state(state)
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
        execution::ensure_execution_tables(conn)?;
        conn.query_row(
            "SELECT participant_id, intent_id, intent_hash, capability, message_id, status\n             FROM execution_receipts\n             WHERE participant_id = ?1 AND intent_id = ?2",
            rusqlite::params![participant_id, intent_id],
            |row| {
                Ok(execution::ExecutionReceipt {
                    participant_id: row.get(0)?,
                    intent_id: row.get(1)?,
                    intent_hash: row.get(2)?,
                    capability: row.get(3)?,
                    message_id: row.get(4)?,
                    status: row.get(5)?,
                })
            },
        )
        .optional()
    })
    .await?
    .ok_or_else(|| AccessApiError::new(StatusCode::NOT_FOUND, "execution_not_found"))?;

    Ok(json_response(StatusCode::OK, json!({"execution": receipt})))
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
