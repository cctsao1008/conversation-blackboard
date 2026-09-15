use std::error::Error;

use rusqlite::{params, Connection, OptionalExtension, Transaction};

use crate::{authorization, execution, execution::Principal, identity};

pub type AdministrationResult<T> = Result<T, Box<dyn Error + Send + Sync>>;

const MAX_PROVIDER_BYTES: usize = 256;
const MAX_SUBJECT_BYTES: usize = 256;
const MAX_CAPABILITY_BYTES: usize = 128;
const MAX_RESOURCE_BYTES: usize = 256;
const MAX_SURFACE_BYTES: usize = 64;

#[derive(Debug, Clone, Copy)]
pub struct AuthorizationAdministrationActor<'a> {
    pub surface: &'a str,
    pub principal: Option<&'a Principal>,
    pub participant_id: Option<&'a str>,
}

impl AuthorizationAdministrationActor<'static> {
    pub const fn local_cli() -> Self {
        Self {
            surface: "local-cli",
            principal: None,
            participant_id: None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct DurableGrantCreateRequest<'a> {
    pub principal_provider: &'a str,
    pub principal_subject: &'a str,
    pub participant_id: &'a str,
    pub capability: &'a str,
    pub resource: Option<&'a str>,
}

#[derive(Debug, Clone)]
pub struct DelegatedGrantCreateRequest<'a> {
    pub principal_provider: &'a str,
    pub principal_subject: &'a str,
    pub participant_id: &'a str,
    pub capability: &'a str,
    pub resource: Option<&'a str>,
    pub intent_id: Option<&'a str>,
    pub expires_at: Option<i64>,
    pub one_shot: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DurableGrantCreateState {
    Created,
    Existing,
    Reactivated,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DurableGrantCreateOutcome {
    pub id: i64,
    pub state: DurableGrantCreateState,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DelegatedGrantCreateOutcome {
    pub id: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GrantDeactivateOutcome {
    pub rows_changed: usize,
}

#[derive(Debug)]
struct NormalizedActor {
    surface: String,
    provider: Option<String>,
    subject: Option<String>,
    participant_id: Option<String>,
}

#[derive(Debug)]
struct GrantScope<'a> {
    principal_provider: &'a str,
    principal_subject: &'a str,
    participant_id: &'a str,
    capability: &'a str,
    resource: Option<&'a str>,
    intent_id: Option<&'a str>,
    expires_at: Option<i64>,
    one_shot: bool,
}

#[derive(Debug)]
struct AdministrationEvent<'a> {
    grant_store: &'a str,
    grant_id: i64,
    operation: &'a str,
    scope: GrantScope<'a>,
    before_status: Option<&'a str>,
    after_status: &'a str,
}

#[derive(Debug)]
struct DelegatedGrantState {
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
    intent_id: Option<String>,
    expires_at: Option<i64>,
    one_shot: bool,
    status: String,
}

pub fn migrate_schema(conn: &Connection) -> rusqlite::Result<()> {
    authorization::ensure_grant_schema(conn)?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS authorization_admin_events (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            grant_store                TEXT NOT NULL CHECK (grant_store IN ('durable', 'delegated')),
            grant_id                   INTEGER NOT NULL,
            operation                  TEXT NOT NULL CHECK (operation IN ('create', 'reactivate', 'deactivate')),
            actor_surface              TEXT NOT NULL,
            actor_provider             TEXT,
            actor_subject              TEXT,
            actor_participant_id       TEXT,
            target_principal_provider  TEXT NOT NULL,
            target_principal_subject   TEXT NOT NULL,
            participant_id             TEXT NOT NULL,
            capability                 TEXT NOT NULL,
            resource                   TEXT,
            intent_id                  TEXT,
            expires_at                 INTEGER,
            one_shot                   INTEGER NOT NULL CHECK (one_shot IN (0, 1)),
            before_status              TEXT CHECK (before_status IS NULL OR before_status IN ('active', 'inactive')),
            after_status               TEXT NOT NULL CHECK (after_status IN ('active', 'inactive')),
            created_at                 INTEGER NOT NULL DEFAULT (unixepoch()),
            CHECK (
                (actor_provider IS NULL AND actor_subject IS NULL)
                OR (actor_provider IS NOT NULL AND actor_subject IS NOT NULL)
            )
        );
        CREATE INDEX IF NOT EXISTS idx_authorization_admin_events_grant
            ON authorization_admin_events (grant_store, grant_id, id);",
    )
}

pub fn schema_current(conn: &Connection) -> rusqlite::Result<bool> {
    Ok(
        authorization::authorization_policy_snapshot_schema_current(conn)?
            && table_has_columns(conn, "delegated_grants", &["updated_at"])?
            && table_has_columns(
                conn,
                "authorization_admin_events",
                &[
                    "id",
                    "grant_store",
                    "grant_id",
                    "operation",
                    "actor_surface",
                    "actor_provider",
                    "actor_subject",
                    "actor_participant_id",
                    "target_principal_provider",
                    "target_principal_subject",
                    "participant_id",
                    "capability",
                    "resource",
                    "intent_id",
                    "expires_at",
                    "one_shot",
                    "before_status",
                    "after_status",
                    "created_at",
                ],
            )?,
    )
}

fn require_schema_current(conn: &Connection) -> AdministrationResult<()> {
    if !schema_current(conn)? {
        return Err(
            "authorization administration schema requires migration: run db init first".into(),
        );
    }
    Ok(())
}

fn table_has_columns(conn: &Connection, table: &str, required: &[&str]) -> rusqlite::Result<bool> {
    let exists: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?1",
        [table],
        |row| row.get(0),
    )?;
    if exists == 0 {
        return Ok(false);
    }
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let columns = stmt
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(required
        .iter()
        .all(|required| columns.iter().any(|column| column == required)))
}

pub fn create_durable_grant(
    conn: &Connection,
    actor: &AuthorizationAdministrationActor<'_>,
    request: &DurableGrantCreateRequest<'_>,
) -> AdministrationResult<DurableGrantCreateOutcome> {
    require_schema_current(conn)?;
    let actor = normalize_actor(actor)?;
    let principal_provider = normalize(
        request.principal_provider,
        MAX_PROVIDER_BYTES,
        "principal_provider",
    )?;
    let principal_subject = normalize(
        request.principal_subject,
        MAX_SUBJECT_BYTES,
        "principal_subject",
    )?;
    let participant_id = identity::validate_participant_id(request.participant_id)
        .ok_or("invalid participant_id")?;
    let capability = normalize(request.capability, MAX_CAPABILITY_BYTES, "capability")?;
    if !authorization::is_known_capability(&capability) {
        return Err(format!("unsupported capability: {capability}").into());
    }
    let resource = normalize_optional(request.resource, MAX_RESOURCE_BYTES, "resource")?;

    let tx = conn.unchecked_transaction()?;
    require_active_participant(&tx, &participant_id)?;
    let rows = {
        let mut stmt = tx.prepare(
            "SELECT id, status
             FROM principal_grants
             WHERE principal_provider = ?1
               AND principal_subject = ?2
               AND participant_id = ?3
               AND capability = ?4
               AND resource IS ?5
             ORDER BY id",
        )?;
        let rows = stmt
            .query_map(
                params![
                    &principal_provider,
                    &principal_subject,
                    &participant_id,
                    &capability,
                    resource.as_deref(),
                ],
                |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };

    if let Some((id, _)) = rows.iter().find(|(_, status)| status == "active") {
        let outcome = DurableGrantCreateOutcome {
            id: *id,
            state: DurableGrantCreateState::Existing,
        };
        tx.commit()?;
        return Ok(outcome);
    }

    let scope = GrantScope {
        principal_provider: &principal_provider,
        principal_subject: &principal_subject,
        participant_id: &participant_id,
        capability: &capability,
        resource: resource.as_deref(),
        intent_id: None,
        expires_at: None,
        one_shot: false,
    };

    if let Some((id, _)) = rows.first() {
        tx.execute(
            "UPDATE principal_grants
             SET status = 'active', updated_at = unixepoch()
             WHERE id = ?1",
            [id],
        )?;
        record_event(
            &tx,
            &actor,
            &AdministrationEvent {
                grant_store: "durable",
                grant_id: *id,
                operation: "reactivate",
                scope,
                before_status: Some("inactive"),
                after_status: "active",
            },
        )?;
        let outcome = DurableGrantCreateOutcome {
            id: *id,
            state: DurableGrantCreateState::Reactivated,
        };
        tx.commit()?;
        return Ok(outcome);
    }

    tx.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        params![
            principal_provider,
            principal_subject,
            participant_id,
            capability,
            resource,
        ],
    )?;
    let id = tx.last_insert_rowid();
    record_event(
        &tx,
        &actor,
        &AdministrationEvent {
            grant_store: "durable",
            grant_id: id,
            operation: "create",
            scope,
            before_status: None,
            after_status: "active",
        },
    )?;
    tx.commit()?;
    Ok(DurableGrantCreateOutcome {
        id,
        state: DurableGrantCreateState::Created,
    })
}

pub fn deactivate_durable_grant(
    conn: &Connection,
    actor: &AuthorizationAdministrationActor<'_>,
    grant_id: i64,
) -> AdministrationResult<Option<GrantDeactivateOutcome>> {
    require_schema_current(conn)?;
    if grant_id <= 0 {
        return Err("grant_id must be positive".into());
    }
    let actor = normalize_actor(actor)?;
    let tx = conn.unchecked_transaction()?;
    let scope: Option<(String, String, String, String, Option<String>)> = tx
        .query_row(
            "SELECT principal_provider, principal_subject, participant_id, capability, resource
             FROM principal_grants WHERE id = ?1",
            [grant_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                ))
            },
        )
        .optional()?;
    let Some((provider, subject, participant_id, capability, resource)) = scope else {
        tx.commit()?;
        return Ok(None);
    };

    let active_ids = {
        let mut stmt = tx.prepare(
            "SELECT id FROM principal_grants
             WHERE principal_provider = ?1
               AND principal_subject = ?2
               AND participant_id = ?3
               AND capability = ?4
               AND resource IS ?5
               AND status != 'inactive'
             ORDER BY id",
        )?;
        let rows = stmt
            .query_map(
                params![
                    &provider,
                    &subject,
                    &participant_id,
                    &capability,
                    resource.as_deref(),
                ],
                |row| row.get::<_, i64>(0),
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };

    let updated = tx.execute(
        "UPDATE principal_grants
         SET status = 'inactive', updated_at = unixepoch()
         WHERE principal_provider = ?1
           AND principal_subject = ?2
           AND participant_id = ?3
           AND capability = ?4
           AND resource IS ?5
           AND status != 'inactive'",
        params![
            &provider,
            &subject,
            &participant_id,
            &capability,
            resource.as_deref(),
        ],
    )?;

    for id in &active_ids {
        record_event(
            &tx,
            &actor,
            &AdministrationEvent {
                grant_store: "durable",
                grant_id: *id,
                operation: "deactivate",
                scope: GrantScope {
                    principal_provider: &provider,
                    principal_subject: &subject,
                    participant_id: &participant_id,
                    capability: &capability,
                    resource: resource.as_deref(),
                    intent_id: None,
                    expires_at: None,
                    one_shot: false,
                },
                before_status: Some("active"),
                after_status: "inactive",
            },
        )?;
    }
    debug_assert_eq!(updated, active_ids.len());
    tx.commit()?;
    Ok(Some(GrantDeactivateOutcome {
        rows_changed: updated,
    }))
}

pub fn create_delegated_grant(
    conn: &Connection,
    actor: &AuthorizationAdministrationActor<'_>,
    request: &DelegatedGrantCreateRequest<'_>,
) -> AdministrationResult<DelegatedGrantCreateOutcome> {
    require_schema_current(conn)?;
    let actor = normalize_actor(actor)?;
    let principal_provider = normalize(
        request.principal_provider,
        MAX_PROVIDER_BYTES,
        "principal_provider",
    )?;
    let principal_subject = normalize(
        request.principal_subject,
        MAX_SUBJECT_BYTES,
        "principal_subject",
    )?;
    let participant_id = identity::validate_participant_id(request.participant_id)
        .ok_or("invalid participant_id")?;
    let capability = normalize(request.capability, MAX_CAPABILITY_BYTES, "capability")?;
    let resource = normalize_optional(request.resource, MAX_RESOURCE_BYTES, "resource")?;
    let intent_id = request
        .intent_id
        .map(|value| execution::normalize_intent_id(value).map_err(|_| "invalid intent_id"))
        .transpose()?;

    let tx = conn.unchecked_transaction()?;
    require_active_participant(&tx, &participant_id)?;
    if let Some(expires_at) = request.expires_at {
        let now: i64 = tx.query_row("SELECT unixepoch()", [], |row| row.get(0))?;
        if expires_at <= now {
            return Err("expires_at must be a future Unix timestamp".into());
        }
    }

    tx.execute(
        "INSERT INTO delegated_grants
            (principal_provider, principal_subject, participant_id, capability, resource, intent_id, expires_at, one_shot)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            principal_provider,
            principal_subject,
            participant_id,
            capability,
            resource,
            intent_id,
            request.expires_at,
            i64::from(request.one_shot),
        ],
    )?;
    let id = tx.last_insert_rowid();
    record_event(
        &tx,
        &actor,
        &AdministrationEvent {
            grant_store: "delegated",
            grant_id: id,
            operation: "create",
            scope: GrantScope {
                principal_provider: &principal_provider,
                principal_subject: &principal_subject,
                participant_id: &participant_id,
                capability: &capability,
                resource: resource.as_deref(),
                intent_id: intent_id.as_deref(),
                expires_at: request.expires_at,
                one_shot: request.one_shot,
            },
            before_status: None,
            after_status: "active",
        },
    )?;
    tx.commit()?;
    Ok(DelegatedGrantCreateOutcome { id })
}

pub fn deactivate_delegated_grant(
    conn: &Connection,
    actor: &AuthorizationAdministrationActor<'_>,
    grant_id: i64,
) -> AdministrationResult<Option<GrantDeactivateOutcome>> {
    require_schema_current(conn)?;
    if grant_id <= 0 {
        return Err("grant_id must be positive".into());
    }
    let actor = normalize_actor(actor)?;
    let tx = conn.unchecked_transaction()?;
    let scope: Option<DelegatedGrantState> = tx
        .query_row(
            "SELECT principal_provider, principal_subject, participant_id, capability,
                    resource, intent_id, expires_at, one_shot, status
             FROM delegated_grants WHERE id = ?1",
            [grant_id],
            |row| {
                Ok(DelegatedGrantState {
                    principal_provider: row.get(0)?,
                    principal_subject: row.get(1)?,
                    participant_id: row.get(2)?,
                    capability: row.get(3)?,
                    resource: row.get(4)?,
                    intent_id: row.get(5)?,
                    expires_at: row.get(6)?,
                    one_shot: row.get::<_, i64>(7)? != 0,
                    status: row.get(8)?,
                })
            },
        )
        .optional()?;
    let Some(scope) = scope else {
        tx.commit()?;
        return Ok(None);
    };

    let updated = tx.execute(
        "UPDATE delegated_grants
         SET status = 'inactive', updated_at = unixepoch()
         WHERE id = ?1 AND status != 'inactive'",
        [grant_id],
    )?;
    if updated == 1 {
        record_event(
            &tx,
            &actor,
            &AdministrationEvent {
                grant_store: "delegated",
                grant_id,
                operation: "deactivate",
                scope: GrantScope {
                    principal_provider: &scope.principal_provider,
                    principal_subject: &scope.principal_subject,
                    participant_id: &scope.participant_id,
                    capability: &scope.capability,
                    resource: scope.resource.as_deref(),
                    intent_id: scope.intent_id.as_deref(),
                    expires_at: scope.expires_at,
                    one_shot: scope.one_shot,
                },
                before_status: Some(&scope.status),
                after_status: "inactive",
            },
        )?;
    }
    tx.commit()?;
    Ok(Some(GrantDeactivateOutcome {
        rows_changed: updated,
    }))
}

fn normalize_actor(
    actor: &AuthorizationAdministrationActor<'_>,
) -> AdministrationResult<NormalizedActor> {
    let surface = normalize(actor.surface, MAX_SURFACE_BYTES, "actor_surface")?;
    let (provider, subject) = match actor.principal {
        Some(principal) => (
            Some(normalize(
                &principal.provider,
                MAX_PROVIDER_BYTES,
                "actor_provider",
            )?),
            Some(normalize(
                &principal.subject,
                MAX_SUBJECT_BYTES,
                "actor_subject",
            )?),
        ),
        None => (None, None),
    };
    let participant_id = actor
        .participant_id
        .map(|value| identity::validate_participant_id(value).ok_or("invalid actor_participant_id"))
        .transpose()?;
    Ok(NormalizedActor {
        surface,
        provider,
        subject,
        participant_id,
    })
}

fn require_active_participant(conn: &Connection, participant_id: &str) -> AdministrationResult<()> {
    let participant_status: Option<String> = conn
        .query_row(
            "SELECT status FROM web_participants WHERE participant_id = ?1",
            [participant_id],
            |row| row.get(0),
        )
        .optional()?;
    match participant_status.as_deref() {
        Some("active") => Ok(()),
        Some(_) => Err(format!("participant is not active: {participant_id}").into()),
        None => Err(format!("unknown participant: {participant_id}").into()),
    }
}

fn record_event(
    tx: &Transaction<'_>,
    actor: &NormalizedActor,
    event: &AdministrationEvent<'_>,
) -> rusqlite::Result<()> {
    tx.execute(
        "INSERT INTO authorization_admin_events
            (grant_store, grant_id, operation, actor_surface, actor_provider, actor_subject,
             actor_participant_id, target_principal_provider, target_principal_subject,
             participant_id, capability, resource, intent_id, expires_at, one_shot,
             before_status, after_status)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)",
        params![
            event.grant_store,
            event.grant_id,
            event.operation,
            actor.surface,
            actor.provider.as_deref(),
            actor.subject.as_deref(),
            actor.participant_id.as_deref(),
            event.scope.principal_provider,
            event.scope.principal_subject,
            event.scope.participant_id,
            event.scope.capability,
            event.scope.resource,
            event.scope.intent_id,
            event.scope.expires_at,
            i64::from(event.scope.one_shot),
            event.before_status,
            event.after_status,
        ],
    )?;
    Ok(())
}

fn normalize(value: &str, max_bytes: usize, field: &'static str) -> AdministrationResult<String> {
    let value = value.trim();
    if value.is_empty() || value.len() > max_bytes || value.chars().any(char::is_control) {
        return Err(format!("invalid {field}").into());
    }
    Ok(value.to_owned())
}

fn normalize_optional(
    value: Option<&str>,
    max_bytes: usize,
    field: &'static str,
) -> AdministrationResult<Option<String>> {
    value
        .map(|value| normalize(value, max_bytes, field))
        .transpose()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db;
    use tempfile::tempdir;

    fn setup() -> (tempfile::TempDir, Connection) {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        identity::provision_web_participant_identity(&conn, "maker-main", "maker", Some("Maker"))
            .unwrap()
            .unwrap();
        (dir, conn)
    }

    fn event_count(conn: &Connection) -> i64 {
        conn.query_row(
            "SELECT COUNT(*) FROM authorization_admin_events",
            [],
            |row| row.get(0),
        )
        .unwrap()
    }

    #[test]
    fn durable_lifecycle_is_atomic_and_records_only_effective_mutations() {
        let (_dir, conn) = setup();
        let actor = AuthorizationAdministrationActor::local_cli();
        let request = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: Some("control-systems"),
        };

        let created = create_durable_grant(&conn, &actor, &request).unwrap();
        assert_eq!(created.state, DurableGrantCreateState::Created);
        assert_eq!(event_count(&conn), 1);

        let existing = create_durable_grant(&conn, &actor, &request).unwrap();
        assert_eq!(existing.id, created.id);
        assert_eq!(existing.state, DurableGrantCreateState::Existing);
        assert_eq!(event_count(&conn), 1);

        let deactivated = deactivate_durable_grant(&conn, &actor, created.id)
            .unwrap()
            .unwrap();
        assert_eq!(deactivated.rows_changed, 1);
        assert_eq!(event_count(&conn), 2);

        let repeated = deactivate_durable_grant(&conn, &actor, created.id)
            .unwrap()
            .unwrap();
        assert_eq!(repeated.rows_changed, 0);
        assert_eq!(event_count(&conn), 2);

        let reactivated = create_durable_grant(&conn, &actor, &request).unwrap();
        assert_eq!(reactivated.id, created.id);
        assert_eq!(reactivated.state, DurableGrantCreateState::Reactivated);
        assert_eq!(event_count(&conn), 3);

        let operations = conn
            .prepare("SELECT operation FROM authorization_admin_events ORDER BY id")
            .unwrap()
            .query_map([], |row| row.get::<_, String>(0))
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap();
        assert_eq!(operations, vec!["create", "deactivate", "reactivate"]);
    }

    #[test]
    fn delegated_lifecycle_records_scope_and_authenticated_actor_without_credentials() {
        let (_dir, conn) = setup();
        let principal = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "admin-agent".to_owned(),
        };
        let actor = AuthorizationAdministrationActor {
            surface: "test-remote",
            principal: Some(&principal),
            participant_id: Some("maker-main"),
        };
        let now: i64 = conn
            .query_row("SELECT unixepoch()", [], |row| row.get(0))
            .unwrap();
        let request = DelegatedGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "maker-main",
            capability: "post_message",
            resource: Some("control-systems"),
            intent_id: Some("intent-107"),
            expires_at: Some(now + 3600),
            one_shot: true,
        };
        let created = create_delegated_grant(&conn, &actor, &request).unwrap();
        assert_eq!(event_count(&conn), 1);
        let row: (String, String, String, String, String, String, i64) = conn
            .query_row(
                "SELECT actor_surface, actor_provider, actor_subject, target_principal_subject,
                        intent_id, capability, one_shot
                 FROM authorization_admin_events WHERE grant_id = ?1 AND grant_store = 'delegated'",
                [created.id],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                        row.get(5)?,
                        row.get(6)?,
                    ))
                },
            )
            .unwrap();
        assert_eq!(row.0, "test-remote");
        assert_eq!(row.1, "oidc:https://issuer.example");
        assert_eq!(row.2, "admin-agent");
        assert_eq!(row.3, "agent-1");
        assert_eq!(row.4, "intent-107");
        assert_eq!(row.5, "post_message");
        assert_eq!(row.6, 1);

        let deactivated = deactivate_delegated_grant(&conn, &actor, created.id)
            .unwrap()
            .unwrap();
        assert_eq!(deactivated.rows_changed, 1);
        assert_eq!(event_count(&conn), 2);
        let repeated = deactivate_delegated_grant(&conn, &actor, created.id)
            .unwrap()
            .unwrap();
        assert_eq!(repeated.rows_changed, 0);
        assert_eq!(event_count(&conn), 2);

        let columns = conn
            .prepare("PRAGMA table_info(authorization_admin_events)")
            .unwrap()
            .query_map([], |row| row.get::<_, String>(1))
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap();
        assert!(!columns.iter().any(|name| {
            let lower = name.to_ascii_lowercase();
            lower.contains("token") || lower.contains("secret") || lower.contains("password")
        }));
    }

    #[test]
    fn local_cli_actor_is_explicit_without_fabricated_principal() {
        let (_dir, conn) = setup();
        let actor = AuthorizationAdministrationActor::local_cli();
        let request = DurableGrantCreateRequest {
            principal_provider: "participant-hmac",
            principal_subject: "maker-main",
            participant_id: "maker-main",
            capability: authorization::READ_MESSAGES,
            resource: None,
        };
        create_durable_grant(&conn, &actor, &request).unwrap();
        let row: (String, Option<String>, Option<String>, Option<String>) = conn
            .query_row(
                "SELECT actor_surface, actor_provider, actor_subject, actor_participant_id
                 FROM authorization_admin_events LIMIT 1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .unwrap();
        assert_eq!(row.0, "local-cli");
        assert_eq!(row.1, None);
        assert_eq!(row.2, None);
        assert_eq!(row.3, None);
    }

    #[test]
    fn failed_provenance_insert_rolls_back_policy_mutation() {
        let (_dir, conn) = setup();
        conn.execute_batch(
            "CREATE TRIGGER reject_authorization_admin_event
             BEFORE INSERT ON authorization_admin_events
             BEGIN
                 SELECT RAISE(ABORT, 'forced administration provenance failure');
             END;",
        )
        .unwrap();
        let actor = AuthorizationAdministrationActor::local_cli();
        let request = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-rollback",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: Some("rollback-scope"),
        };
        assert!(create_durable_grant(&conn, &actor, &request).is_err());
        let grants: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM principal_grants WHERE principal_subject = 'agent-rollback'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(grants, 0);
        assert_eq!(event_count(&conn), 0);
    }

    #[test]
    fn invalid_request_creates_neither_grant_nor_event() {
        let (_dir, conn) = setup();
        let actor = AuthorizationAdministrationActor::local_cli();
        let request = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "missing-main",
            capability: authorization::POST_MESSAGE,
            resource: None,
        };
        assert!(create_durable_grant(&conn, &actor, &request).is_err());
        assert_eq!(event_count(&conn), 0);
        let grants: i64 = conn
            .query_row("SELECT COUNT(*) FROM principal_grants", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(grants, 0);
    }
}
