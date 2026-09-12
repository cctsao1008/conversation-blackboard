use std::{env, path::PathBuf, sync::OnceLock};

use axum::{
    body::Bytes,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use regex::Regex;
use ring::hmac;
use rusqlite::{params, Connection, OptionalExtension};
use serde::Deserialize;
use serde_json::json;

use crate::{db, identity, model::Identity};

const GITHUB_PROVIDER: &str = "github";
const TITLE_PREFIX: &str = "[blackboard]";

#[derive(Clone, Debug)]
pub struct GithubWebhookState {
    db_path: PathBuf,
    webhook_secret: Option<String>,
    repository_id: Option<u64>,
}

impl GithubWebhookState {
    pub fn from_env(db_path: PathBuf) -> Self {
        let webhook_secret = env::var("BLACKBOARD_GITHUB_WEBHOOK_SECRET")
            .ok()
            .filter(|value| !value.is_empty());
        let repository_id = env::var("BLACKBOARD_GITHUB_REPOSITORY_ID")
            .ok()
            .and_then(|value| value.parse::<u64>().ok());
        Self {
            db_path,
            webhook_secret,
            repository_id,
        }
    }

    #[cfg(test)]
    fn configured(db_path: PathBuf, webhook_secret: &str, repository_id: u64) -> Self {
        Self {
            db_path,
            webhook_secret: Some(webhook_secret.to_owned()),
            repository_id: Some(repository_id),
        }
    }
}

pub fn app(state: GithubWebhookState) -> Router {
    Router::new()
        .route("/integrations/github/issues", post(github_issue_webhook))
        .with_state(state)
}

pub fn ensure_owner_columns(conn: &Connection) -> rusqlite::Result<()> {
    for (name, definition) in [
        ("owner_provider", "TEXT"),
        ("owner_subject", "TEXT"),
        ("owner_login", "TEXT"),
    ] {
        if !table_has_column(conn, "web_participants", name)? {
            conn.execute(
                &format!("ALTER TABLE web_participants ADD COLUMN {name} {definition}"),
                [],
            )?;
        }
    }
    Ok(())
}

pub fn set_participant_owner(
    conn: &Connection,
    participant_id: &str,
    provider: &str,
    subject: &str,
    login: Option<&str>,
) -> rusqlite::Result<bool> {
    ensure_owner_columns(conn)?;
    if identity::validate_participant_id(participant_id).is_none()
        || provider.trim() != GITHUB_PROVIDER
        || subject.trim().is_empty()
        || !subject.bytes().all(|byte| byte.is_ascii_digit())
        || login.is_some_and(|value| value.trim().is_empty() || value.chars().count() > 128)
    {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants\n         SET owner_provider = ?1, owner_subject = ?2, owner_login = ?3, updated_at = unixepoch()\n         WHERE participant_id = ?4",
        params![provider.trim(), subject.trim(), login.map(str::trim), participant_id],
    )?;
    Ok(changed == 1)
}

pub fn clear_participant_owner(conn: &Connection, participant_id: &str) -> rusqlite::Result<bool> {
    ensure_owner_columns(conn)?;
    if identity::validate_participant_id(participant_id).is_none() {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants\n         SET owner_provider = NULL, owner_subject = NULL, owner_login = NULL, updated_at = unixepoch()\n         WHERE participant_id = ?1",
        [participant_id],
    )?;
    Ok(changed == 1)
}

async fn github_issue_webhook(
    State(state): State<GithubWebhookState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let (Some(secret), Some(expected_repository_id)) =
        (state.webhook_secret.as_deref(), state.repository_id)
    else {
        return error_response(
            StatusCode::SERVICE_UNAVAILABLE,
            "github_webhook_not_configured",
        );
    };

    if headers
        .get("x-github-event")
        .and_then(|value| value.to_str().ok())
        != Some("issues")
    {
        return error_response(StatusCode::BAD_REQUEST, "unsupported_github_event");
    }

    let Some(signature) = headers
        .get("x-hub-signature-256")
        .and_then(|value| value.to_str().ok())
    else {
        return error_response(StatusCode::UNAUTHORIZED, "invalid_github_signature");
    };
    if !verify_github_signature(secret, signature, &body) {
        return error_response(StatusCode::UNAUTHORIZED, "invalid_github_signature");
    }

    let event: GithubIssueEvent = match serde_json::from_slice(&body) {
        Ok(value) => value,
        Err(_) => return error_response(StatusCode::BAD_REQUEST, "invalid_github_payload"),
    };
    if event.action != "opened" {
        return error_response(
            StatusCode::UNPROCESSABLE_ENTITY,
            "unsupported_github_action",
        );
    }
    if event.repository.id != expected_repository_id {
        return error_response(StatusCode::FORBIDDEN, "github_repository_mismatch");
    }
    if !event.issue.title.starts_with(TITLE_PREFIX) {
        return error_response(StatusCode::UNPROCESSABLE_ENTITY, "not_blackboard_issue");
    }
    if event.sender.id != event.issue.user.id {
        return error_response(StatusCode::FORBIDDEN, "github_actor_mismatch");
    }
    if !matches!(
        event.issue.author_association.as_str(),
        "OWNER" | "MEMBER" | "COLLABORATOR"
    ) {
        return error_response(StatusCode::FORBIDDEN, "github_author_not_admitted");
    }

    let Some(issue_body) = event.issue.body.as_deref() else {
        return error_response(StatusCode::BAD_REQUEST, "invalid_blackboard_intent");
    };
    let intent: BlackboardIssueIntent = match serde_json::from_str(issue_body) {
        Ok(value) => value,
        Err(_) => return error_response(StatusCode::BAD_REQUEST, "invalid_blackboard_intent"),
    };
    if identity::validate_participant_id(&intent.participant_id).is_none()
        || !channel_re().is_match(&intent.channel)
        || intent.body.trim().is_empty()
        || intent.body.len() > crate::http::MAX_BODY_BYTES
    {
        return error_response(StatusCode::BAD_REQUEST, "invalid_blackboard_intent");
    }
    let kind = intent.kind.as_deref().unwrap_or("message");
    if !kind_re().is_match(kind) {
        return error_response(StatusCode::BAD_REQUEST, "invalid_blackboard_intent");
    }
    if intent.reply_to.is_some_and(|value| value <= 0) {
        return error_response(StatusCode::BAD_REQUEST, "invalid_blackboard_intent");
    }

    let participant_id = intent.participant_id.clone();
    let owner_subject = event.sender.id.to_string();
    let channel = intent.channel.clone();
    let kind = kind.to_owned();
    let message_body = intent.body.clone();
    let reply_to = intent.reply_to;
    let nonce = format!(
        "github:{}:issue:{}",
        event.repository.id, event.issue.number
    );
    let request_hash = identity::hash_token(
        &json!({
            "channel": channel,
            "kind": kind,
            "body": message_body,
            "reply_to": reply_to,
        })
        .to_string(),
    );
    let db_path = state.db_path.clone();

    let write_result =
        tokio::task::spawn_blocking(move || -> rusqlite::Result<WebhookWriteResult> {
            let conn = db::connect(&db_path)?;
            ensure_owner_columns(&conn)?;
            let identity = get_owned_active_participant(&conn, &participant_id, &owner_subject)?
                .ok_or(rusqlite::Error::QueryReturnedNoRows)?;
            if !db::ensure_channel_for_write(&conn, &channel, Some(&identity.instance))? {
                return Ok(WebhookWriteResult::ChannelArchived);
            }
            Ok(
                match db::append_navigation_message(
                    &conn,
                    &identity,
                    db::NavigationMessageInput {
                        channel: &channel,
                        kind: &kind,
                        body: &message_body,
                        reply_to,
                        nonce: &nonce,
                        request_hash: &request_hash,
                    },
                )? {
                    db::NavigationAppendResult::Created(message) => {
                        WebhookWriteResult::Created(message.id)
                    }
                    db::NavigationAppendResult::Existing(message) => {
                        WebhookWriteResult::Existing(message.id)
                    }
                    db::NavigationAppendResult::NonceConflict => WebhookWriteResult::NonceConflict,
                    db::NavigationAppendResult::ReplyTargetNotFound => {
                        WebhookWriteResult::ReplyTargetNotFound
                    }
                },
            )
        })
        .await;

    let result = match write_result {
        Ok(Ok(value)) => value,
        Ok(Err(rusqlite::Error::QueryReturnedNoRows)) => {
            return error_response(StatusCode::FORBIDDEN, "github_participant_not_owned")
        }
        Ok(Err(_)) | Err(_) => {
            return error_response(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable")
        }
    };

    let payload = |status: &str, id: i64| {
        Json(json!({
            "status": status,
            "id": id,
            "participant_id": intent.participant_id,
            "github_user_id": event.sender.id,
            "github_login": event.sender.login,
        }))
        .into_response()
    };

    match result {
        WebhookWriteResult::Created(id) => {
            (StatusCode::CREATED, payload("created", id)).into_response()
        }
        WebhookWriteResult::Existing(id) => payload("existing", id),
        WebhookWriteResult::NonceConflict => error_response(StatusCode::CONFLICT, "nonce_conflict"),
        WebhookWriteResult::ReplyTargetNotFound => {
            error_response(StatusCode::BAD_REQUEST, "reply_target_not_found")
        }
        WebhookWriteResult::ChannelArchived => {
            error_response(StatusCode::CONFLICT, "channel_archived")
        }
    }
}

fn get_owned_active_participant(
    conn: &Connection,
    participant_id: &str,
    owner_subject: &str,
) -> rusqlite::Result<Option<Identity>> {
    conn.query_row(
        "SELECT source, participant_id, label\n         FROM web_participants\n         WHERE participant_id = ?1\n           AND status = 'active'\n           AND owner_provider = 'github'\n           AND owner_subject = ?2\n         LIMIT 1",
        params![participant_id, owner_subject],
        |row| {
            Ok(Identity {
                source: row.get(0)?,
                instance: row.get(1)?,
                label: row.get(2)?,
            })
        },
    )
    .optional()
}

fn verify_github_signature(secret: &str, signature: &str, body: &[u8]) -> bool {
    let Some(encoded) = signature.strip_prefix("sha256=") else {
        return false;
    };
    let Some(proof) = decode_hex_32(encoded) else {
        return false;
    };
    let key = hmac::Key::new(hmac::HMAC_SHA256, secret.as_bytes());
    hmac::verify(&key, body, &proof).is_ok()
}

fn decode_hex_32(value: &str) -> Option<[u8; 32]> {
    if value.len() != 64 {
        return None;
    }
    let mut out = [0_u8; 32];
    for (index, chunk) in value.as_bytes().as_chunks::<2>().0.iter().enumerate() {
        let high = hex_nibble(chunk[0])?;
        let low = hex_nibble(chunk[1])?;
        out[index] = (high << 4) | low;
    }
    Some(out)
}

fn hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn table_has_column(conn: &Connection, table: &str, column: &str) -> rusqlite::Result<bool> {
    let sql = format!("PRAGMA table_info({table})");
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map([], |row| row.get::<_, String>(1))?;
    for name in rows {
        if name? == column {
            return Ok(true);
        }
    }
    Ok(false)
}

fn channel_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$").unwrap())
}

fn kind_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$").unwrap())
}

fn error_response(status: StatusCode, code: &'static str) -> Response {
    (status, Json(json!({"error": code}))).into_response()
}

#[derive(Debug, Deserialize)]
struct GithubIssueEvent {
    action: String,
    repository: GithubRepository,
    sender: GithubActor,
    issue: GithubIssue,
}

#[derive(Debug, Deserialize)]
struct GithubRepository {
    id: u64,
}

#[derive(Debug, Deserialize)]
struct GithubActor {
    id: u64,
    login: String,
}

#[derive(Debug, Deserialize)]
struct GithubIssue {
    number: i64,
    title: String,
    body: Option<String>,
    author_association: String,
    user: GithubActor,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BlackboardIssueIntent {
    participant_id: String,
    channel: String,
    #[serde(default)]
    kind: Option<String>,
    body: String,
    #[serde(default)]
    reply_to: Option<i64>,
}

#[derive(Debug)]
enum WebhookWriteResult {
    Created(i64),
    Existing(i64),
    NonceConflict,
    ReplyTargetNotFound,
    ChannelArchived,
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        body::{to_bytes, Body},
        http::Request,
    };
    use ring::hmac;
    use tempfile::tempdir;
    use tower::ServiceExt;

    const SECRET: &str = "webhook-secret-for-tests";
    const REPO_ID: u64 = 1364516460;

    fn sign(body: &[u8]) -> String {
        let key = hmac::Key::new(hmac::HMAC_SHA256, SECRET.as_bytes());
        let bytes = hmac::sign(&key, body);
        let mut out = String::from("sha256=");
        for byte in bytes.as_ref() {
            use std::fmt::Write as _;
            let _ = write!(&mut out, "{byte:02x}");
        }
        out
    }

    fn payload(sender_id: u64, association: &str, participant_id: &str) -> Vec<u8> {
        serde_json::to_vec(&json!({
            "action": "opened",
            "repository": {"id": REPO_ID},
            "sender": {"id": sender_id, "login": "alice"},
            "issue": {
                "number": 77,
                "title": "[blackboard] write blackboard-lounge",
                "body": serde_json::to_string(&json!({
                    "participant_id": participant_id,
                    "channel": "blackboard-lounge",
                    "kind": "message",
                    "body": "hello from github",
                    "reply_to": null
                })).unwrap(),
                "author_association": association,
                "user": {"id": sender_id, "login": "alice"}
            }
        }))
        .unwrap()
    }

    async fn send(
        router: Router,
        body: Vec<u8>,
        signature: String,
    ) -> (StatusCode, serde_json::Value) {
        let response = router
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/integrations/github/issues")
                    .header("content-type", "application/json")
                    .header("x-github-event", "issues")
                    .header("x-hub-signature-256", signature)
                    .body(Body::from(body))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        let bytes = to_bytes(response.into_body(), 64 * 1024).await.unwrap();
        (status, serde_json::from_slice(&bytes).unwrap())
    }

    fn fixture() -> (tempfile::TempDir, PathBuf, Router) {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        ensure_owner_columns(&conn).unwrap();
        identity::provision_web_participant_identity(&conn, "alice-main", "alice", None)
            .unwrap()
            .unwrap();
        set_participant_owner(&conn, "alice-main", "github", "123456", Some("alice")).unwrap();
        let router = app(GithubWebhookState::configured(
            path.clone(),
            SECRET,
            REPO_ID,
        ));
        (dir, path, router)
    }

    #[tokio::test]
    async fn valid_owned_collaborator_write_is_idempotent() {
        let (_dir, path, router) = fixture();
        let body = payload(123456, "COLLABORATOR", "alice-main");
        let (status, first) = send(router.clone(), body.clone(), sign(&body)).await;
        assert_eq!(status, StatusCode::CREATED);
        assert_eq!(first["status"], "created");
        let (status, second) = send(router, body.clone(), sign(&body)).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(second["status"], "existing");
        assert_eq!(first["id"], second["id"]);

        let conn = db::connect(&path).unwrap();
        let (source, instance): (String, String) = conn
            .query_row(
                "SELECT source, instance FROM messages WHERE id = ?1",
                [first["id"].as_i64().unwrap()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(source, "alice");
        assert_eq!(instance, "alice-main");
    }

    #[tokio::test]
    async fn wrong_owner_and_non_collaborator_are_rejected() {
        let (_dir, _path, router) = fixture();
        let wrong_owner = payload(999999, "COLLABORATOR", "alice-main");
        let (status, _) = send(router.clone(), wrong_owner.clone(), sign(&wrong_owner)).await;
        assert_eq!(status, StatusCode::FORBIDDEN);

        let outsider = payload(123456, "NONE", "alice-main");
        let (status, _) = send(router, outsider.clone(), sign(&outsider)).await;
        assert_eq!(status, StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn invalid_signature_is_rejected_before_payload_authorization() {
        let (_dir, _path, router) = fixture();
        let body = payload(123456, "COLLABORATOR", "alice-main");
        let (status, response) = send(router, body, "sha256=00".to_owned()).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
        assert_eq!(response["error"], "invalid_github_signature");
    }
}
