from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

path = Path('src/authorization.rs')
text = path.read_text()

anchor = '''#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationPolicySnapshot {
    pub durable_grants: Vec<DurableGrantSnapshot>,
    pub delegated_grants: Vec<DelegatedGrantSnapshot>,
}
'''
insert = anchor + r'''

pub const DEFAULT_AUTHORIZATION_POLICY_WINDOW_SIZE: usize = 20;
pub const MAX_AUTHORIZATION_POLICY_WINDOW_SIZE: usize = 200;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthorizationPolicyStore {
    Durable,
    Delegated,
}

impl AuthorizationPolicyStore {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "durable" => Some(Self::Durable),
            "delegated" => Some(Self::Delegated),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Durable => "durable",
            Self::Delegated => "delegated",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthorizationPolicyWindowOrder {
    Desc,
    Asc,
}

impl AuthorizationPolicyWindowOrder {
    pub fn parse(value: Option<&str>) -> Option<Self> {
        match value {
            None | Some("desc") => Some(Self::Desc),
            Some("asc") => Some(Self::Asc),
            Some(_) => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Desc => "desc",
            Self::Asc => "asc",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AuthorizationPolicyWindowRequest<'a> {
    pub store: &'a str,
    pub before: Option<i64>,
    pub after: Option<i64>,
    pub limit: Option<usize>,
    pub order: Option<&'a str>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AuthorizationPolicyWindow {
    pub store: String,
    pub durable_grants: Vec<DurableGrantSnapshot>,
    pub delegated_grants: Vec<DelegatedGrantSnapshot>,
    pub order: String,
    pub has_more: bool,
}
'''
text = replace_once(text, anchor, insert, 'policy window types')

reader_anchor = '''pub fn authorization_integrity_schema_current(conn: &Connection) -> rusqlite::Result<bool> {'''
reader = r'''
fn validate_policy_window_request(
    request: AuthorizationPolicyWindowRequest<'_>,
) -> rusqlite::Result<(AuthorizationPolicyStore, AuthorizationPolicyWindowOrder, usize)> {
    let store = AuthorizationPolicyStore::parse(request.store).ok_or(rusqlite::Error::InvalidQuery)?;
    let order = AuthorizationPolicyWindowOrder::parse(request.order)
        .ok_or(rusqlite::Error::InvalidQuery)?;
    if request.before.is_some_and(|value| value <= 0)
        || request.after.is_some_and(|value| value <= 0)
        || (request.before.is_some() && request.after.is_some())
        || (order == AuthorizationPolicyWindowOrder::Desc && request.after.is_some())
        || (order == AuthorizationPolicyWindowOrder::Asc && request.before.is_some())
    {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let limit = request.limit.unwrap_or(DEFAULT_AUTHORIZATION_POLICY_WINDOW_SIZE);
    if !(1..=MAX_AUTHORIZATION_POLICY_WINDOW_SIZE).contains(&limit) {
        return Err(rusqlite::Error::InvalidQuery);
    }
    Ok((store, order, limit))
}

pub fn read_authorization_policy_window(
    conn: &Connection,
    request: AuthorizationPolicyWindowRequest<'_>,
) -> rusqlite::Result<AuthorizationPolicyWindow> {
    if !authorization_policy_snapshot_schema_current(conn)? {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let (store, order, limit) = validate_policy_window_request(request)?;
    let fetch_limit = i64::try_from(limit + 1).map_err(|_| rusqlite::Error::InvalidQuery)?;

    let (durable_grants, delegated_grants, has_more) = match store {
        AuthorizationPolicyStore::Durable => {
            let mut grants = read_durable_grant_window(
                conn,
                order,
                request.before,
                request.after,
                fetch_limit,
            )?;
            let has_more = grants.len() > limit;
            grants.truncate(limit);
            (grants, Vec::new(), has_more)
        }
        AuthorizationPolicyStore::Delegated => {
            let mut grants = read_delegated_grant_window(
                conn,
                order,
                request.before,
                request.after,
                fetch_limit,
            )?;
            let has_more = grants.len() > limit;
            grants.truncate(limit);
            (Vec::new(), grants, has_more)
        }
    };

    Ok(AuthorizationPolicyWindow {
        store: store.as_str().to_owned(),
        durable_grants,
        delegated_grants,
        order: order.as_str().to_owned(),
        has_more,
    })
}

fn read_durable_grant_window(
    conn: &Connection,
    order: AuthorizationPolicyWindowOrder,
    before: Option<i64>,
    after: Option<i64>,
    fetch_limit: i64,
) -> rusqlite::Result<Vec<DurableGrantSnapshot>> {
    let (sql, cursor) = match order {
        AuthorizationPolicyWindowOrder::Desc => match before {
            Some(cursor) => (
                "SELECT id, principal_provider, principal_subject, participant_id, capability,\n                        resource, status, created_at, updated_at\n                 FROM principal_grants\n                 WHERE id < ?1\n                 ORDER BY id DESC\n                 LIMIT ?2",
                Some(cursor),
            ),
            None => (
                "SELECT id, principal_provider, principal_subject, participant_id, capability,\n                        resource, status, created_at, updated_at\n                 FROM principal_grants\n                 ORDER BY id DESC\n                 LIMIT ?1",
                None,
            ),
        },
        AuthorizationPolicyWindowOrder::Asc => match after {
            Some(cursor) => (
                "SELECT id, principal_provider, principal_subject, participant_id, capability,\n                        resource, status, created_at, updated_at\n                 FROM principal_grants\n                 WHERE id > ?1\n                 ORDER BY id ASC\n                 LIMIT ?2",
                Some(cursor),
            ),
            None => (
                "SELECT id, principal_provider, principal_subject, participant_id, capability,\n                        resource, status, created_at, updated_at\n                 FROM principal_grants\n                 ORDER BY id ASC\n                 LIMIT ?1",
                None,
            ),
        },
    };
    let mut stmt = conn.prepare(sql)?;
    let map_row = |row: &rusqlite::Row<'_>| {
        Ok(DurableGrantSnapshot {
            id: row.get(0)?,
            principal_provider: row.get(1)?,
            principal_subject: row.get(2)?,
            participant_id: row.get(3)?,
            capability: row.get(4)?,
            resource: row.get(5)?,
            status: row.get(6)?,
            created_at: row.get(7)?,
            updated_at: row.get(8)?,
        })
    };
    match cursor {
        Some(cursor) => stmt
            .query_map(params![cursor, fetch_limit], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>(),
        None => stmt
            .query_map(params![fetch_limit], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>(),
    }
}

fn read_delegated_grant_window(
    conn: &Connection,
    order: AuthorizationPolicyWindowOrder,
    before: Option<i64>,
    after: Option<i64>,
    fetch_limit: i64,
) -> rusqlite::Result<Vec<DelegatedGrantSnapshot>> {
    let (sql, cursor) = match order {
        AuthorizationPolicyWindowOrder::Desc => match before {
            Some(cursor) => (
                "SELECT id, principal_provider, principal_subject, participant_id, capability,\n                        resource, intent_id, expires_at, one_shot, consumed_at,\n                        consumed_intent_id, status\n                 FROM delegated_grants\n                 WHERE id < ?1\n                 ORDER BY id DESC\n                 LIMIT ?2",
                Some(cursor),
            ),
            None => (
                "SELECT id, principal_provider, principal_subject, participant_id, capability,\n                        resource, intent_id, expires_at, one_shot, consumed_at,\n                        consumed_intent_id, status\n                 FROM delegated_grants\n                 ORDER BY id DESC\n                 LIMIT ?1",
                None,
            ),
        },
        AuthorizationPolicyWindowOrder::Asc => match after {
            Some(cursor) => (
                "SELECT id, principal_provider, principal_subject, participant_id, capability,\n                        resource, intent_id, expires_at, one_shot, consumed_at,\n                        consumed_intent_id, status\n                 FROM delegated_grants\n                 WHERE id > ?1\n                 ORDER BY id ASC\n                 LIMIT ?2",
                Some(cursor),
            ),
            None => (
                "SELECT id, principal_provider, principal_subject, participant_id, capability,\n                        resource, intent_id, expires_at, one_shot, consumed_at,\n                        consumed_intent_id, status\n                 FROM delegated_grants\n                 ORDER BY id ASC\n                 LIMIT ?1",
                None,
            ),
        },
    };
    let mut stmt = conn.prepare(sql)?;
    let map_row = |row: &rusqlite::Row<'_>| {
        Ok(DelegatedGrantSnapshot {
            id: row.get(0)?,
            principal_provider: row.get(1)?,
            principal_subject: row.get(2)?,
            participant_id: row.get(3)?,
            capability: row.get(4)?,
            resource: row.get(5)?,
            intent_id: row.get(6)?,
            expires_at: row.get(7)?,
            one_shot: row.get::<_, i64>(8)? != 0,
            consumed_at: row.get(9)?,
            consumed_intent_id: row.get(10)?,
            status: row.get(11)?,
        })
    };
    match cursor {
        Some(cursor) => stmt
            .query_map(params![cursor, fetch_limit], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>(),
        None => stmt
            .query_map(params![fetch_limit], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>(),
    }
}

'''
text = replace_once(text, reader_anchor, reader + reader_anchor, 'policy window reader')

test_anchor = '''    #[test]
    fn authorization_policy_snapshot_refuses_legacy_schema_without_mutation() {'''
tests = r'''    #[test]
    fn authorization_policy_window_is_bounded_and_store_selected() {
        let (_dir, conn) = setup();
        for index in 0..25 {
            conn.execute(
                "INSERT INTO principal_grants
                    (principal_provider, principal_subject, participant_id, capability, resource, status)
                 VALUES ('oidc:https://issuer.example', ?1, 'maker-main', 'read_messages', NULL, ?2)",
                params![
                    format!("durable-{index:02}"),
                    if index == 3 { "inactive" } else { "active" }
                ],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO delegated_grants
                    (principal_provider, principal_subject, participant_id, capability, resource,
                     intent_id, one_shot, consumed_at, consumed_intent_id, status)
                 VALUES ('oidc:https://issuer.example', ?1, 'maker-main', 'post_message', NULL,
                         ?2, ?3, ?4, ?5, ?6)",
                params![
                    format!("delegated-{index:02}"),
                    format!("intent-{index:02}"),
                    if index == 4 { 1 } else { 0 },
                    if index == 4 { Some(1000_i64) } else { None },
                    if index == 4 { Some("intent-04") } else { None },
                    if index == 4 { "inactive" } else { "active" },
                ],
            )
            .unwrap();
        }

        let durable = read_authorization_policy_window(
            &conn,
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: None,
                after: None,
                limit: None,
                order: None,
            },
        )
        .unwrap();
        assert_eq!(durable.store, "durable");
        assert_eq!(durable.order, "desc");
        assert_eq!(durable.durable_grants.len(), DEFAULT_AUTHORIZATION_POLICY_WINDOW_SIZE);
        assert!(durable.delegated_grants.is_empty());
        assert!(durable.has_more);
        assert!(durable.durable_grants.windows(2).all(|pair| pair[0].id > pair[1].id));

        let delegated = read_authorization_policy_window(
            &conn,
            AuthorizationPolicyWindowRequest {
                store: "delegated",
                before: None,
                after: None,
                limit: Some(5),
                order: Some("asc"),
            },
        )
        .unwrap();
        assert_eq!(delegated.store, "delegated");
        assert!(delegated.durable_grants.is_empty());
        assert_eq!(delegated.delegated_grants.len(), 5);
        assert!(delegated.has_more);
        assert!(delegated.delegated_grants.windows(2).all(|pair| pair[0].id < pair[1].id));
    }

    #[test]
    fn authorization_policy_window_keyset_pages_without_duplicates_or_skips() {
        let (_dir, conn) = setup();
        for index in 0..9 {
            conn.execute(
                "INSERT INTO principal_grants
                    (principal_provider, principal_subject, participant_id, capability)
                 VALUES ('oidc:https://issuer.example', ?1, 'maker-main', 'read_messages')",
                params![format!("durable-page-{index}")],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO delegated_grants
                    (principal_provider, principal_subject, participant_id, capability, intent_id)
                 VALUES ('oidc:https://issuer.example', ?1, 'maker-main', 'post_message', ?2)",
                params![format!("delegated-page-{index}"), format!("page-intent-{index}")],
            )
            .unwrap();
        }

        let first = read_authorization_policy_window(
            &conn,
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: None,
                after: None,
                limit: Some(4),
                order: Some("desc"),
            },
        )
        .unwrap();
        let before = first.durable_grants.last().unwrap().id;
        let second = read_authorization_policy_window(
            &conn,
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: Some(before),
                after: None,
                limit: Some(4),
                order: Some("desc"),
            },
        )
        .unwrap();
        let before = second.durable_grants.last().unwrap().id;
        let third = read_authorization_policy_window(
            &conn,
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: Some(before),
                after: None,
                limit: Some(4),
                order: Some("desc"),
            },
        )
        .unwrap();
        assert!(first.has_more);
        assert!(second.has_more);
        assert!(!third.has_more);
        let ids = first
            .durable_grants
            .iter()
            .chain(second.durable_grants.iter())
            .chain(third.durable_grants.iter())
            .map(|grant| grant.id)
            .collect::<Vec<_>>();
        assert_eq!(ids.len(), 9);
        let unique = ids.iter().copied().collect::<HashSet<_>>();
        assert_eq!(unique.len(), 9);

        let first = read_authorization_policy_window(
            &conn,
            AuthorizationPolicyWindowRequest {
                store: "delegated",
                before: None,
                after: None,
                limit: Some(4),
                order: Some("asc"),
            },
        )
        .unwrap();
        let after = first.delegated_grants.last().unwrap().id;
        let second = read_authorization_policy_window(
            &conn,
            AuthorizationPolicyWindowRequest {
                store: "delegated",
                before: None,
                after: Some(after),
                limit: Some(4),
                order: Some("asc"),
            },
        )
        .unwrap();
        let after = second.delegated_grants.last().unwrap().id;
        let third = read_authorization_policy_window(
            &conn,
            AuthorizationPolicyWindowRequest {
                store: "delegated",
                before: None,
                after: Some(after),
                limit: Some(4),
                order: Some("asc"),
            },
        )
        .unwrap();
        assert!(first.has_more);
        assert!(second.has_more);
        assert!(!third.has_more);
        let ids = first
            .delegated_grants
            .iter()
            .chain(second.delegated_grants.iter())
            .chain(third.delegated_grants.iter())
            .map(|grant| grant.id)
            .collect::<Vec<_>>();
        assert_eq!(ids.len(), 9);
        let unique = ids.iter().copied().collect::<HashSet<_>>();
        assert_eq!(unique.len(), 9);
    }

    #[test]
    fn authorization_policy_window_rejects_invalid_cursor_direction_and_limit() {
        let (_dir, conn) = setup();
        for request in [
            AuthorizationPolicyWindowRequest {
                store: "unknown",
                before: None,
                after: None,
                limit: None,
                order: None,
            },
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: Some(1),
                after: Some(2),
                limit: None,
                order: None,
            },
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: None,
                after: Some(1),
                limit: None,
                order: Some("desc"),
            },
            AuthorizationPolicyWindowRequest {
                store: "delegated",
                before: Some(1),
                after: None,
                limit: None,
                order: Some("asc"),
            },
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: None,
                after: None,
                limit: Some(0),
                order: None,
            },
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: None,
                after: None,
                limit: Some(MAX_AUTHORIZATION_POLICY_WINDOW_SIZE + 1),
                order: None,
            },
        ] {
            assert!(read_authorization_policy_window(&conn, request).is_err());
        }
    }

    #[test]
    fn authorization_policy_window_refuses_legacy_schema_without_mutation() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE principal_grants (
                id INTEGER PRIMARY KEY,
                principal_provider TEXT NOT NULL,
                principal_subject TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                resource TEXT,
                status TEXT NOT NULL
            );
            CREATE TABLE delegated_grants (
                id INTEGER PRIMARY KEY,
                principal_provider TEXT NOT NULL,
                principal_subject TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                resource TEXT,
                intent_id TEXT,
                expires_at INTEGER,
                one_shot INTEGER NOT NULL,
                consumed_at INTEGER,
                consumed_intent_id TEXT,
                status TEXT NOT NULL
            );",
        )
        .unwrap();
        assert!(read_authorization_policy_window(
            &conn,
            AuthorizationPolicyWindowRequest {
                store: "durable",
                before: None,
                after: None,
                limit: None,
                order: None,
            },
        )
        .is_err());
        assert!(!table_has_columns(&conn, "principal_grants", &["created_at"]).unwrap());
    }

'''
text = replace_once(text, test_anchor, tests + test_anchor, 'policy window tests')
path.write_text(text)
