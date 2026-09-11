use std::{collections::HashMap, path::PathBuf, sync::OnceLock};

use axum::{
    body::{to_bytes, Body},
    extract::{Path, State},
    http::{header, HeaderMap, HeaderName, HeaderValue, Request, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use regex::Regex;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

use crate::{db, identity, model::Identity, request_auth};

pub const MAX_BODY_BYTES: usize = 64 * 1024;
pub const MAX_PAGE_SIZE: usize = 200;

#[derive(Clone)]
pub struct AppState {
    pub db_path: PathBuf,
    pub registration_key: Option<String>,
}

#[derive(Debug)]
struct ApiError {
    status: StatusCode,
    code: &'static str,
}

impl ApiError {
    fn new(status: StatusCode, code: &'static str) -> Self {
        Self { status, code }
    }

    fn unauthorized() -> Self {
        Self::new(StatusCode::UNAUTHORIZED, "unauthorized")
    }

    fn database() -> Self {
        Self::new(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable")
    }

    fn internal() -> Self {
        Self::new(StatusCode::INTERNAL_SERVER_ERROR, "internal_error")
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        json_response(self.status, json!({"error": self.code}))
    }
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/app.js", get(app_js))
        .route("/style.css", get(style_css))
        .route("/utcp", get(utcp_manual))
        .route("/r/{channel}", get(navigation_read))
        .route("/w/{participant_id}", get(navigation_write))
        .route("/api/health", get(health))
        .route("/api/whoami", get(whoami))
        .route("/api/messages", get(messages).post(post_message))
        .route("/api/channels", get(channels))
        .route("/api/register", post(register))
        .fallback(not_found)
        .with_state(state)
}

async fn index() -> Response {
    static_response(
        "text/html; charset=utf-8",
        include_str!("../web/index.html"),
    )
}

async fn app_js() -> Response {
    static_response(
        "text/javascript; charset=utf-8",
        include_str!("../web/app.js"),
    )
}

async fn style_css() -> Response {
    static_response("text/css; charset=utf-8", include_str!("../web/style.css"))
}

async fn utcp_manual() -> Response {
    static_response(
        "application/json; charset=utf-8",
        include_str!("../integrations/utcp.json"),
    )
}

async fn navigation_read(
    State(state): State<AppState>,
    Path(channel): Path<String>,
    uri: Uri,
) -> Result<Response, ApiError> {
    if !name_re().is_match(&channel) {
        return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_channel"));
    }

    let params = first_query_values(&uri);
    let after = parse_after(&params)?;
    let limit = parse_limit(&params, 50)?;
    let query_channel = channel.clone();
    let rows = with_db(&state, move |conn| {
        db::list_messages_after(conn, after, Some(&query_channel), limit)
    })
    .await?;

    let latest_id = rows.last().map(|row| row.id).unwrap_or(after);
    let mut body = format!(
        "conversation-blackboard read\nchannel: {channel}\nafter: {after}\ncount: {}\nlatest_id: {latest_id}\n",
        rows.len()
    );
    for row in rows {
        body.push('\n');
        body.push_str(
            &serde_json::to_string(&row)
                .map_err(|_| ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, "internal_error"))?,
        );
        body.push('\n');
    }

    Ok(navigation_text_response(StatusCode::OK, body))
}

async fn navigation_write(
    State(state): State<AppState>,
    Path(participant_id): Path<String>,
    uri: Uri,
) -> Result<Response, ApiError> {
    if identity::validate_participant_id(&participant_id).is_none() {
        return Err(ApiError::unauthorized());
    }

    let params = first_query_values(&uri);
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

    let request_hash = identity::hash_token(
        &json!({
            "channel": channel,
            "kind": kind,
            "body": message_body,
            "reply_to": reply_to,
        })
        .to_string(),
    );

    let write_identity = identity.clone();
    let write_channel = channel.clone();
    let write_kind = kind.clone();
    let write_body = message_body.clone();
    let write_nonce = nonce.clone();
    let result = with_db(&state, move |conn| {
        db::append_navigation_message(
            conn,
            &write_identity,
            db::NavigationMessageInput {
                channel: &write_channel,
                kind: &write_kind,
                body: &write_body,
                reply_to,
                nonce: &write_nonce,
                request_hash: &request_hash,
            },
        )
    })
    .await?;

    let (status, persisted, idempotent) = match result {
        db::NavigationAppendResult::Created(message) => ("created", message, false),
        db::NavigationAppendResult::Existing(message) => ("existing", message, true),
        db::NavigationAppendResult::NonceConflict => {
            return Ok(navigation_text_response(
                StatusCode::CONFLICT,
                "conversation-blackboard write\nstatus: error\nerror: nonce_conflict\n".to_owned(),
            ));
        }
        db::NavigationAppendResult::ReplyTargetNotFound => {
            return Ok(navigation_text_response(
                StatusCode::BAD_REQUEST,
                "conversation-blackboard write\nstatus: error\nerror: reply_target_not_found\n"
                    .to_owned(),
            ));
        }
    };

    let reply = persisted
        .reply_to
        .map(|value| value.to_string())
        .unwrap_or_else(|| "null".to_owned());
    let body = format!(
        "conversation-blackboard write\nstatus: {status}\nidempotent: {idempotent}\nid: {}\nsource: {}\nparticipant_id: {}\ninstance: {}\nchannel: {}\nkind: {}\nreply_to: {reply}\n",
        persisted.id,
        persisted.source,
        persisted.instance,
        persisted.instance,
        persisted.channel,
        persisted.kind
    );
    Ok(navigation_text_response(StatusCode::OK, body))
}

async fn health(State(state): State<AppState>) -> Result<Response, ApiError> {
    with_db(&state, db::health).await?;
    Ok(json_response(StatusCode::OK, json!({"status": "ok"})))
}

async fn whoami(State(state): State<AppState>, headers: HeaderMap) -> Result<Response, ApiError> {
    let identity = require_identity(&state, &headers).await?;
    Ok(json_response(
        StatusCode::OK,
        json!({
            "source": identity.source,
            "instance": identity.instance,
            "label": identity.label,
        }),
    ))
}

async fn messages(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Response, ApiError> {
    let _identity = require_identity(&state, &headers).await?;
    let params = first_query_values(&uri);

    let after = parse_after(&params)?;
    let limit = parse_limit(&params, 100)?;

    let channel = params.get("channel").cloned();
    if let Some(value) = &channel {
        if !name_re().is_match(value) {
            return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_channel"));
        }
    }

    let rows = with_db(&state, move |conn| {
        db::list_messages_after(conn, after, channel.as_deref(), limit)
    })
    .await?;

    Ok(json_response(StatusCode::OK, json!({"messages": rows})))
}

async fn channels(State(state): State<AppState>, headers: HeaderMap) -> Result<Response, ApiError> {
    let _identity = require_identity(&state, &headers).await?;
    let rows = with_db(&state, db::list_channels).await?;
    Ok(json_response(StatusCode::OK, json!({"channels": rows})))
}

async fn register(
    State(state): State<AppState>,
    headers: HeaderMap,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    let expected = state
        .registration_key
        .as_deref()
        .filter(|value| !value.is_empty())
        .ok_or_else(ApiError::unauthorized)?;
    let supplied = headers
        .get("X-Registration-Key")
        .and_then(|value| value.to_str().ok())
        .ok_or_else(ApiError::unauthorized)?;

    if !constant_time_text_eq(expected, supplied) {
        return Err(ApiError::unauthorized());
    }

    let body = read_json_object(request).await?;
    let source = match body.get("source") {
        Some(Value::String(value)) => value.clone(),
        _ => return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_source")),
    };

    let label = match body.get("label") {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) if value.chars().count() <= 256 => Some(value.clone()),
        _ => return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_label")),
    };

    if identity::validate_source(&source).is_none() {
        return Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "invalid_registration",
        ));
    }
    if identity::normalize_label(label.as_deref()).is_err() {
        return Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "invalid_registration",
        ));
    }

    let (identity, token) = with_db(&state, move |conn| {
        identity::register_identity(conn, &source, label.as_deref())
    })
    .await?;

    Ok(json_response(
        StatusCode::CREATED,
        json!({
            "source": identity.source,
            "instance": identity.instance,
            "label": identity.label,
            "token": token,
        }),
    ))
}

async fn post_message(
    State(state): State<AppState>,
    headers: HeaderMap,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    let identity = require_identity(&state, &headers).await?;
    let body = read_json_object(request).await?;

    if body.contains_key("source") || body.contains_key("instance") {
        return Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "identity_is_server_resolved",
        ));
    }

    let channel = body
        .get("channel")
        .and_then(Value::as_str)
        .filter(|value| name_re().is_match(value))
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_channel"))?
        .to_owned();

    let kind = match body.get("kind") {
        None => "message".to_owned(),
        Some(Value::String(value)) if kind_re().is_match(value) => value.clone(),
        _ => return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_kind")),
    };

    let message_body = body
        .get("body")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_body"))?
        .to_owned();

    if message_body.len() > MAX_BODY_BYTES {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "request_too_large",
        ));
    }

    let reply_to = match body.get("reply_to") {
        None | Some(Value::Null) => None,
        Some(Value::Number(value)) => value
            .as_i64()
            .filter(|id| *id > 0)
            .map(Some)
            .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_reply_to"))?,
        _ => return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_reply_to")),
    };

    if let Some(target) = reply_to {
        let exists = with_db(&state, move |conn| db::message_exists(conn, target)).await?;
        if !exists {
            return Err(ApiError::new(
                StatusCode::BAD_REQUEST,
                "reply_target_not_found",
            ));
        }
    }

    let row = with_db(&state, move |conn| {
        db::append_message(conn, &identity, &channel, &kind, &message_body, reply_to)
    })
    .await?;

    Ok(json_response(StatusCode::CREATED, json!({"message": row})))
}

async fn not_found() -> Response {
    json_response(StatusCode::NOT_FOUND, json!({"error": "not_found"}))
}

async fn read_json_object(request: Request<Body>) -> Result<Map<String, Value>, ApiError> {
    let bytes = to_bytes(request.into_body(), MAX_BODY_BYTES)
        .await
        .map_err(|_| ApiError::new(StatusCode::PAYLOAD_TOO_LARGE, "request_too_large"))?;
    if bytes.is_empty() {
        return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_json"));
    }
    let value: Value = serde_json::from_slice(&bytes)
        .map_err(|_| ApiError::new(StatusCode::BAD_REQUEST, "invalid_json"))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_json"))
}

async fn require_identity(state: &AppState, headers: &HeaderMap) -> Result<Identity, ApiError> {
    let headers = headers.clone();
    let identity = with_db(state, move |conn| {
        request_auth::resolve_request_identity(conn, &headers)
    })
    .await?;
    identity.ok_or_else(ApiError::unauthorized)
}

fn constant_time_text_eq(expected: &str, supplied: &str) -> bool {
    let expected = Sha256::digest(expected.as_bytes());
    let supplied = Sha256::digest(supplied.as_bytes());
    bool::from(expected.ct_eq(&supplied))
}

fn first_query_values(uri: &Uri) -> HashMap<String, String> {
    let mut values = HashMap::new();
    if let Some(query) = uri.query() {
        for (key, value) in url::form_urlencoded::parse(query.as_bytes()) {
            if value.is_empty() {
                continue;
            }
            values
                .entry(key.into_owned())
                .or_insert_with(|| value.into_owned());
        }
    }
    values
}

fn parse_after(params: &HashMap<String, String>) -> Result<i64, ApiError> {
    let after = params
        .get("after")
        .map(String::as_str)
        .unwrap_or("0")
        .parse::<i64>()
        .map_err(|_| ApiError::new(StatusCode::BAD_REQUEST, "invalid_query"))?;
    if after < 0 {
        return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_query"));
    }
    Ok(after)
}

fn parse_limit(params: &HashMap<String, String>, default: usize) -> Result<usize, ApiError> {
    let limit = params
        .get("limit")
        .map(String::as_str)
        .unwrap_or_else(|| if default == 50 { "50" } else { "100" })
        .parse::<usize>()
        .map_err(|_| ApiError::new(StatusCode::BAD_REQUEST, "invalid_query"))?;
    if !(1..=MAX_PAGE_SIZE).contains(&limit) {
        return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_query"));
    }
    Ok(limit)
}

fn parse_reply_to(params: &HashMap<String, String>) -> Result<Option<i64>, ApiError> {
    match params.get("reply_to") {
        None => Ok(None),
        Some(value) => value
            .parse::<i64>()
            .ok()
            .filter(|id| *id > 0)
            .map(Some)
            .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_reply_to")),
    }
}

fn name_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$").unwrap())
}

fn kind_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$").unwrap())
}

fn nonce_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$").unwrap())
}

async fn with_db<T, F>(state: &AppState, operation: F) -> Result<T, ApiError>
where
    T: Send + 'static,
    F: FnOnce(&rusqlite::Connection) -> rusqlite::Result<T> + Send + 'static,
{
    let path = state.db_path.clone();
    tokio::task::spawn_blocking(move || {
        let conn = db::connect(&path)?;
        operation(&conn)
    })
    .await
    .map_err(|_| ApiError::internal())?
    .map_err(|_| ApiError::database())
}

fn json_response(status: StatusCode, value: Value) -> Response {
    let mut response = (status, Json(value)).into_response();
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    add_common_security_headers(response.headers_mut());
    response
}

fn navigation_text_response(status: StatusCode, body: String) -> Response {
    let mut response = (status, body).into_response();
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/plain; charset=utf-8"),
    );
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response.headers_mut().insert(
        HeaderName::from_static("x-robots-tag"),
        HeaderValue::from_static("noindex, nofollow"),
    );
    add_common_security_headers(response.headers_mut());
    response
}

fn static_response(content_type: &'static str, body: &'static str) -> Response {
    let mut response = (StatusCode::OK, body).into_response();
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    response.headers_mut().insert(
        HeaderName::from_static("content-security-policy"),
        HeaderValue::from_static(
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        ),
    );
    add_common_security_headers(response.headers_mut());
    response
}

fn add_common_security_headers(headers: &mut HeaderMap) {
    headers.insert(
        HeaderName::from_static("x-content-type-options"),
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        HeaderName::from_static("referrer-policy"),
        HeaderValue::from_static("no-referrer"),
    );
    headers.insert(
        HeaderName::from_static("x-frame-options"),
        HeaderValue::from_static("DENY"),
    );
}
