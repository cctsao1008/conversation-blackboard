use std::sync::OnceLock;

use axum::{
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use regex::Regex;
use rusqlite::{params, Connection, Result as SqlResult};
use serde::Deserialize;
use serde_json::json;

use crate::{db, http::AppState, model::Message, request_auth};

const DEFAULT_WINDOW_SIZE: usize = 20;
const MAX_WINDOW_SIZE: usize = 200;

#[derive(Debug, Deserialize)]
struct MessageWindowQuery {
    channel: String,
    before: Option<i64>,
    limit: Option<usize>,
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/api/messages/window", get(message_window))
        .with_state(state)
}

async fn message_window(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(query): Query<MessageWindowQuery>,
) -> Response {
    if !name_re().is_match(&query.channel) {
        return json_error(StatusCode::BAD_REQUEST, "invalid_channel");
    }

    if query.before.is_some_and(|value| value <= 0) {
        return json_error(StatusCode::BAD_REQUEST, "invalid_query");
    }

    let limit = query.limit.unwrap_or(DEFAULT_WINDOW_SIZE);
    if !(1..=MAX_WINDOW_SIZE).contains(&limit) {
        return json_error(StatusCode::BAD_REQUEST, "invalid_query");
    }

    let db_path = state.db_path.clone();
    let channel = query.channel;
    let before = query.before;
    let auth_headers = headers.clone();

    let result = tokio::task::spawn_blocking(move || -> SqlResult<Option<(Vec<Message>, bool)>> {
        let conn = db::connect(&db_path)?;
        if request_auth::resolve_request_identity(&conn, &auth_headers)?.is_none() {
            return Ok(None);
        }

        let rows = list_message_window(&conn, &channel, before, limit)?;
        let has_older = match rows.first() {
            Some(first) => has_message_before(&conn, &channel, first.id)?,
            None => false,
        };
        Ok(Some((rows, has_older)))
    })
    .await;

    match result {
        Ok(Ok(Some((messages, has_older)))) => Json(json!({
            "messages": messages,
            "has_older": has_older,
        }))
        .into_response(),
        Ok(Ok(None)) => json_error(StatusCode::UNAUTHORIZED, "unauthorized"),
        Ok(Err(_)) => json_error(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable"),
        Err(_) => json_error(StatusCode::INTERNAL_SERVER_ERROR, "internal_error"),
    }
}

fn list_message_window(
    conn: &Connection,
    channel: &str,
    before: Option<i64>,
    limit: usize,
) -> SqlResult<Vec<Message>> {
    let mut out = Vec::new();

    if let Some(before) = before {
        let mut stmt = conn.prepare(
            "SELECT id, created_at, channel, source, instance, kind, body, reply_to\n             FROM messages\n             WHERE channel = ?1 AND id < ?2\n             ORDER BY id DESC\n             LIMIT ?3",
        )?;
        let rows = stmt.query_map(params![channel, before, limit as i64], row_to_message)?;
        for row in rows {
            out.push(row?);
        }
    } else {
        let mut stmt = conn.prepare(
            "SELECT id, created_at, channel, source, instance, kind, body, reply_to\n             FROM messages\n             WHERE channel = ?1\n             ORDER BY id DESC\n             LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![channel, limit as i64], row_to_message)?;
        for row in rows {
            out.push(row?);
        }
    }

    out.reverse();
    Ok(out)
}

fn has_message_before(conn: &Connection, channel: &str, id: i64) -> SqlResult<bool> {
    let exists: i64 = conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM messages WHERE channel = ?1 AND id < ?2)",
        params![channel, id],
        |row| row.get(0),
    )?;
    Ok(exists != 0)
}

fn row_to_message(row: &rusqlite::Row<'_>) -> SqlResult<Message> {
    Ok(Message {
        id: row.get(0)?,
        created_at: row.get(1)?,
        channel: row.get(2)?,
        source: row.get(3)?,
        instance: row.get(4)?,
        kind: row.get(5)?,
        body: row.get(6)?,
        reply_to: row.get(7)?,
    })
}

fn name_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$").unwrap())
}

fn json_error(status: StatusCode, code: &'static str) -> Response {
    (status, Json(json!({"error": code}))).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::identity;
    use axum::{
        body::{to_bytes, Body},
        http::{header, Request},
    };
    use serde_json::Value;
    use tempfile::{tempdir, TempDir};
    use tower::ServiceExt;

    struct Fixture {
        _dir: TempDir,
        router: Router,
        bearer: String,
        ids: Vec<i64>,
    }

    fn fixture() -> Fixture {
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("board.db");
        db::initialize(&db_path).unwrap();
        let conn = db::connect(&db_path).unwrap();
        let (writer, bearer) =
            identity::register_identity(&conn, "window-test", Some("history-test")).unwrap();

        let mut ids = Vec::new();
        for index in 1..=25 {
            let message = db::append_message(
                &conn,
                &writer,
                "control-systems",
                "message",
                &format!("message-{index}"),
                None,
            )
            .unwrap();
            ids.push(message.id);
        }
        db::append_message(&conn, &writer, "other-channel", "message", "other", None).unwrap();
        drop(conn);

        let router = app(AppState {
            db_path,
            registration_key: None,
        });

        Fixture {
            _dir: dir,
            router,
            bearer,
            ids,
        }
    }

    async fn request(fixture: &Fixture, uri: &str) -> Response {
        fixture
            .router
            .clone()
            .oneshot(
                Request::builder()
                    .uri(uri)
                    .header(header::AUTHORIZATION, format!("Bearer {}", fixture.bearer))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap()
    }

    async fn response_json(response: Response) -> (StatusCode, Value) {
        let status = response.status();
        let bytes = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
        (status, serde_json::from_slice(&bytes).unwrap())
    }

    #[tokio::test]
    async fn latest_window_is_bounded_and_chronological() {
        let fixture = fixture();
        let response = request(
            &fixture,
            "/api/messages/window?channel=control-systems&limit=5",
        )
        .await;
        let (status, body) = response_json(response).await;

        assert_eq!(status, StatusCode::OK);
        let rows = body["messages"].as_array().unwrap();
        let returned: Vec<i64> = rows.iter().map(|row| row["id"].as_i64().unwrap()).collect();
        assert_eq!(returned, fixture.ids[20..25]);
        assert_eq!(body["has_older"], Value::Bool(true));
    }

    #[tokio::test]
    async fn before_loads_the_immediately_older_window() {
        let fixture = fixture();
        let before = fixture.ids[20];
        let response = request(
            &fixture,
            &format!("/api/messages/window?channel=control-systems&before={before}&limit=5"),
        )
        .await;
        let (status, body) = response_json(response).await;

        assert_eq!(status, StatusCode::OK);
        let rows = body["messages"].as_array().unwrap();
        let returned: Vec<i64> = rows.iter().map(|row| row["id"].as_i64().unwrap()).collect();
        assert_eq!(returned, fixture.ids[15..20]);
        assert_eq!(body["has_older"], Value::Bool(true));
    }

    #[tokio::test]
    async fn oldest_window_reports_no_more_history() {
        let fixture = fixture();
        let before = fixture.ids[5];
        let response = request(
            &fixture,
            &format!("/api/messages/window?channel=control-systems&before={before}&limit=5"),
        )
        .await;
        let (status, body) = response_json(response).await;

        assert_eq!(status, StatusCode::OK);
        let rows = body["messages"].as_array().unwrap();
        let returned: Vec<i64> = rows.iter().map(|row| row["id"].as_i64().unwrap()).collect();
        assert_eq!(returned, fixture.ids[0..5]);
        assert_eq!(body["has_older"], Value::Bool(false));
    }

    #[tokio::test]
    async fn window_requires_auth_and_valid_query() {
        let fixture = fixture();

        let unauthorized = fixture
            .router
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/messages/window?channel=control-systems")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(unauthorized.status(), StatusCode::UNAUTHORIZED);

        for uri in [
            "/api/messages/window?channel=bad!channel",
            "/api/messages/window?channel=control-systems&before=0",
            "/api/messages/window?channel=control-systems&limit=0",
            "/api/messages/window?channel=control-systems&limit=201",
        ] {
            assert_eq!(
                request(&fixture, uri).await.status(),
                StatusCode::BAD_REQUEST
            );
        }
    }
}
