use std::sync::OnceLock;

use axum::{
    extract::{Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use regex::Regex;
use rusqlite::{params, Connection, Result as SqlResult};
use serde::Deserialize;
use serde_json::json;

use crate::{db, http::AppState, model::Message, request_auth, web_auth};

const DEFAULT_WINDOW_SIZE: usize = 20;
const MAX_WINDOW_SIZE: usize = 200;

#[derive(Debug, Deserialize)]
struct MessageWindowQuery {
    channel: String,
    before: Option<i64>,
    after: Option<i64>,
    limit: Option<usize>,
    order: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MessageOrder {
    Desc,
    Asc,
}

impl MessageOrder {
    fn parse(value: Option<&str>) -> Option<Self> {
        match value.unwrap_or("desc") {
            "desc" => Some(Self::Desc),
            "asc" => Some(Self::Asc),
            _ => None,
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Desc => "desc",
            Self::Asc => "asc",
        }
    }
}

#[derive(Debug)]
enum WindowAccessError {
    Unauthorized,
    Forbidden,
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/api/messages/window", get(message_window))
        .with_state(state)
}

async fn message_window(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<MessageWindowQuery>,
) -> Response {
    if !name_re().is_match(&query.channel) {
        return json_error(StatusCode::BAD_REQUEST, "invalid_channel");
    }

    if query.before.is_some_and(|value| value <= 0) || query.after.is_some_and(|value| value <= 0) {
        return json_error(StatusCode::BAD_REQUEST, "invalid_query");
    }

    let Some(order) = MessageOrder::parse(query.order.as_deref()) else {
        return json_error(StatusCode::BAD_REQUEST, "invalid_order");
    };

    if query.before.is_some() && query.after.is_some() {
        return json_error(StatusCode::BAD_REQUEST, "invalid_query");
    }
    if (order == MessageOrder::Desc && query.after.is_some())
        || (order == MessageOrder::Asc && query.before.is_some())
    {
        return json_error(StatusCode::BAD_REQUEST, "invalid_query");
    }

    let limit = query.limit.unwrap_or(DEFAULT_WINDOW_SIZE);
    if !(1..=MAX_WINDOW_SIZE).contains(&limit) {
        return json_error(StatusCode::BAD_REQUEST, "invalid_query");
    }

    let db_path = state.db_path.clone();
    let channel = query.channel;
    let before = query.before;
    let after = query.after;
    let auth_headers = headers.clone();
    let guest = request_auth::verified_web_session(&headers)
        .is_some_and(|session| session.session_type == web_auth::WebSessionKind::Guest);
    let request_target = uri
        .path_and_query()
        .map(|value| value.as_str())
        .unwrap_or_else(|| uri.path())
        .to_owned();

    let result = tokio::task::spawn_blocking(
        move || -> SqlResult<Result<(Vec<Message>, bool), WindowAccessError>> {
            let conn = db::connect(&db_path)?;
            if guest {
                if !db::channel_is_public_active(&conn, &channel)? {
                    return Ok(Err(WindowAccessError::Forbidden));
                }
            } else if request_auth::resolve_request_identity_for_target(
                &conn,
                &auth_headers,
                "GET",
                &request_target,
            )?
            .is_none()
            {
                return Ok(Err(WindowAccessError::Unauthorized));
            }

            let rows = list_message_window(&conn, &channel, order, before, after, limit)?;
            let has_more = match rows.last() {
                Some(last) => match order {
                    MessageOrder::Desc => has_message_before(&conn, &channel, last.id)?,
                    MessageOrder::Asc => has_message_after(&conn, &channel, last.id)?,
                },
                None => false,
            };
            Ok(Ok((rows, has_more)))
        },
    )
    .await;

    match result {
        Ok(Ok(Ok((messages, has_more)))) => Json(json!({
            "messages": messages,
            "order": order.as_str(),
            "has_more": has_more,
            "has_older": order == MessageOrder::Desc && has_more,
            "has_newer": order == MessageOrder::Asc && has_more,
        }))
        .into_response(),
        Ok(Ok(Err(WindowAccessError::Unauthorized))) => {
            json_error(StatusCode::UNAUTHORIZED, "unauthorized")
        }
        Ok(Ok(Err(WindowAccessError::Forbidden))) => json_error(StatusCode::FORBIDDEN, "forbidden"),
        Ok(Err(_)) => json_error(StatusCode::SERVICE_UNAVAILABLE, "database_unavailable"),
        Err(_) => json_error(StatusCode::INTERNAL_SERVER_ERROR, "internal_error"),
    }
}

fn list_message_window(
    conn: &Connection,
    channel: &str,
    order: MessageOrder,
    before: Option<i64>,
    after: Option<i64>,
    limit: usize,
) -> SqlResult<Vec<Message>> {
    let mut out = Vec::new();

    match order {
        MessageOrder::Desc => {
            if let Some(before) = before {
                let mut stmt = conn.prepare(
                    "SELECT id, created_at, channel, source, instance, kind, body, reply_to\n                     FROM messages\n                     WHERE channel = ?1 AND id < ?2\n                     ORDER BY id DESC\n                     LIMIT ?3",
                )?;
                let rows =
                    stmt.query_map(params![channel, before, limit as i64], row_to_message)?;
                for row in rows {
                    out.push(row?);
                }
            } else {
                let mut stmt = conn.prepare(
                    "SELECT id, created_at, channel, source, instance, kind, body, reply_to\n                     FROM messages\n                     WHERE channel = ?1\n                     ORDER BY id DESC\n                     LIMIT ?2",
                )?;
                let rows = stmt.query_map(params![channel, limit as i64], row_to_message)?;
                for row in rows {
                    out.push(row?);
                }
            }
        }
        MessageOrder::Asc => {
            if let Some(after) = after {
                let mut stmt = conn.prepare(
                    "SELECT id, created_at, channel, source, instance, kind, body, reply_to\n                     FROM messages\n                     WHERE channel = ?1 AND id > ?2\n                     ORDER BY id ASC\n                     LIMIT ?3",
                )?;
                let rows = stmt.query_map(params![channel, after, limit as i64], row_to_message)?;
                for row in rows {
                    out.push(row?);
                }
            } else {
                let mut stmt = conn.prepare(
                    "SELECT id, created_at, channel, source, instance, kind, body, reply_to\n                     FROM messages\n                     WHERE channel = ?1\n                     ORDER BY id ASC\n                     LIMIT ?2",
                )?;
                let rows = stmt.query_map(params![channel, limit as i64], row_to_message)?;
                for row in rows {
                    out.push(row?);
                }
            }
        }
    }

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

fn has_message_after(conn: &Connection, channel: &str, id: i64) -> SqlResult<bool> {
    let exists: i64 = conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM messages WHERE channel = ?1 AND id > ?2)",
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
    use crate::{identity, web_auth};
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
        db::append_message(
            &conn,
            &writer,
            "blackboard-lounge",
            "message",
            "public",
            None,
        )
        .unwrap();
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

    async fn guest_request(fixture: &Fixture, uri: &str) -> Response {
        let session = web_auth::issue_guest_session();
        fixture
            .router
            .clone()
            .oneshot(
                Request::builder()
                    .uri(uri)
                    .header(web_auth::WEB_SESSION_HEADER, session.token)
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

    fn ids(body: &Value) -> Vec<i64> {
        body["messages"]
            .as_array()
            .unwrap()
            .iter()
            .map(|row| row["id"].as_i64().unwrap())
            .collect()
    }

    #[tokio::test]
    async fn default_window_is_newest_first() {
        let fixture = fixture();
        let response = request(
            &fixture,
            "/api/messages/window?channel=control-systems&limit=5",
        )
        .await;
        let (status, body) = response_json(response).await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            ids(&body),
            fixture.ids[20..25]
                .iter()
                .rev()
                .copied()
                .collect::<Vec<_>>()
        );
        assert_eq!(body["order"], "desc");
        assert_eq!(body["has_more"], Value::Bool(true));
        assert_eq!(body["has_older"], Value::Bool(true));
        assert_eq!(body["has_newer"], Value::Bool(false));
    }

    #[tokio::test]
    async fn explicit_desc_before_loads_immediately_older_window() {
        let fixture = fixture();
        let before = fixture.ids[20];
        let response = request(
            &fixture,
            &format!(
                "/api/messages/window?channel=control-systems&order=desc&before={before}&limit=5"
            ),
        )
        .await;
        let (status, body) = response_json(response).await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            ids(&body),
            fixture.ids[15..20]
                .iter()
                .rev()
                .copied()
                .collect::<Vec<_>>()
        );
        assert_eq!(body["has_more"], Value::Bool(true));
    }

    #[tokio::test]
    async fn explicit_asc_starts_oldest_and_after_loads_newer_window() {
        let fixture = fixture();
        let first = request(
            &fixture,
            "/api/messages/window?channel=control-systems&order=asc&limit=5",
        )
        .await;
        let (status, body) = response_json(first).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(ids(&body), fixture.ids[0..5]);
        assert_eq!(body["order"], "asc");
        assert_eq!(body["has_more"], Value::Bool(true));
        assert_eq!(body["has_older"], Value::Bool(false));
        assert_eq!(body["has_newer"], Value::Bool(true));

        let after = fixture.ids[4];
        let second = request(
            &fixture,
            &format!(
                "/api/messages/window?channel=control-systems&order=asc&after={after}&limit=5"
            ),
        )
        .await;
        let (status, body) = response_json(second).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(ids(&body), fixture.ids[5..10]);
    }

    #[tokio::test]
    async fn pagination_has_no_duplicates_or_skips_in_either_direction() {
        let fixture = fixture();

        let first_desc = response_json(
            request(
                &fixture,
                "/api/messages/window?channel=control-systems&order=desc&limit=5",
            )
            .await,
        )
        .await
        .1;
        let desc_ids = ids(&first_desc);
        let second_desc = response_json(
            request(
                &fixture,
                &format!(
                    "/api/messages/window?channel=control-systems&order=desc&before={}&limit=5",
                    desc_ids.last().unwrap()
                ),
            )
            .await,
        )
        .await
        .1;
        let mut combined_desc = desc_ids;
        combined_desc.extend(ids(&second_desc));
        assert_eq!(combined_desc.len(), 10);
        assert!(combined_desc.windows(2).all(|pair| pair[0] > pair[1]));

        let first_asc = response_json(
            request(
                &fixture,
                "/api/messages/window?channel=control-systems&order=asc&limit=5",
            )
            .await,
        )
        .await
        .1;
        let asc_ids = ids(&first_asc);
        let second_asc = response_json(
            request(
                &fixture,
                &format!(
                    "/api/messages/window?channel=control-systems&order=asc&after={}&limit=5",
                    asc_ids.last().unwrap()
                ),
            )
            .await,
        )
        .await
        .1;
        let mut combined_asc = asc_ids;
        combined_asc.extend(ids(&second_asc));
        assert_eq!(combined_asc.len(), 10);
        assert!(combined_asc.windows(2).all(|pair| pair[0] < pair[1]));
    }

    #[tokio::test]
    async fn final_page_reports_no_more_history() {
        let fixture = fixture();
        let before = fixture.ids[5];
        let response = request(
            &fixture,
            &format!(
                "/api/messages/window?channel=control-systems&order=desc&before={before}&limit=5"
            ),
        )
        .await;
        let (status, body) = response_json(response).await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            ids(&body),
            fixture.ids[0..5].iter().rev().copied().collect::<Vec<_>>()
        );
        assert_eq!(body["has_more"], Value::Bool(false));
    }

    #[tokio::test]
    async fn guest_can_read_public_window_but_not_private_window() {
        let fixture = fixture();
        assert_eq!(
            guest_request(
                &fixture,
                "/api/messages/window?channel=blackboard-lounge&order=desc&limit=5",
            )
            .await
            .status(),
            StatusCode::OK
        );
        assert_eq!(
            guest_request(
                &fixture,
                "/api/messages/window?channel=control-systems&order=asc&limit=5",
            )
            .await
            .status(),
            StatusCode::FORBIDDEN
        );
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
            "/api/messages/window?channel=control-systems&after=0",
            "/api/messages/window?channel=control-systems&limit=0",
            "/api/messages/window?channel=control-systems&limit=201",
            "/api/messages/window?channel=control-systems&order=sideways",
            "/api/messages/window?channel=control-systems&order=desc&after=5",
            "/api/messages/window?channel=control-systems&order=asc&before=5",
            "/api/messages/window?channel=control-systems&before=5&after=3",
        ] {
            assert_eq!(
                request(&fixture, uri).await.status(),
                StatusCode::BAD_REQUEST
            );
        }
    }
}
