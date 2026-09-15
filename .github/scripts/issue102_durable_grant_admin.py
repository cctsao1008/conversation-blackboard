from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


# authorization.rs: expose the canonical capability vocabulary without duplicating strings.
p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")
old = '''const KNOWN_CAPABILITIES: [&str; 7] = [
    READ_MESSAGES,
    POST_MESSAGE,
    REPLY,
    READ_EXECUTION_RECEIPT,
    READ_EXECUTION_AUDIT,
    READ_EXECUTION_AUDIT_SWEEP,
    MANAGE_CHANNELS,
];
'''
new = old + '''
pub fn is_known_capability(capability: &str) -> bool {
    KNOWN_CAPABILITIES.contains(&capability)
}
'''
s = replace_once(s, old, new, "authorization capability helper")
p.write_text(s, encoding="utf-8")


# grant_admin.rs: add first-class durable principal-grant lifecycle management.
p = Path("src/grant_admin.rs")
s = p.read_text(encoding="utf-8")
anchor = '''#[derive(Debug, Subcommand)]
pub enum GrantCommand {
'''
insert = r'''#[derive(Debug, Clone, PartialEq, Eq)]
struct DurableGrantInspection {
    id: i64,
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
    status: String,
    created_at: i64,
    updated_at: i64,
}

#[derive(Debug, Clone)]
struct DurableGrantCreateSpec<'a> {
    principal_provider: &'a str,
    principal_subject: &'a str,
    participant_id: &'a str,
    capability: &'a str,
    resource: Option<&'a str>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DurableGrantCreateState {
    Created,
    Existing,
    Reactivated,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct DurableGrantCreateOutcome {
    id: i64,
    state: DurableGrantCreateState,
}

#[derive(Debug, Subcommand)]
pub enum DurableGrantCommand {
    /// Create or reactivate one durable scoped principal grant.
    Create {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        principal_provider: String,
        #[arg(long)]
        principal_subject: String,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        capability: String,
        #[arg(long)]
        resource: Option<String>,
    },
    /// List durable scoped principal grants and non-secret lifecycle state.
    List {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: Option<String>,
    },
    /// Deactivate one durable authority scope without deleting history.
    Deactivate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        grant_id: i64,
    },
}

#[derive(Debug, Subcommand)]
pub enum GrantCommand {
'''
s = replace_once(s, anchor, insert, "durable grant command types")

old = '''    /// Deactivate a delegated grant without deleting history.
    Deactivate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        grant_id: i64,
    },
}
'''
new = '''    /// Deactivate a delegated grant without deleting history.
    Deactivate {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        grant_id: i64,
    },
    /// Manage durable scoped principal grants separately from delegated grants.
    Durable {
        #[command(subcommand)]
        command: DurableGrantCommand,
    },
}
'''
s = replace_once(s, old, new, "durable grant namespace")

old = '''        GrantCommand::Deactivate { db: path, grant_id } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if grant_id <= 0 {
                return Err("grant_id must be positive".into());
            }
            if !deactivate_grant(&conn, grant_id)? {
                return Err(format!("unknown delegated grant: {grant_id}").into());
            }
            println!("DELEGATED GRANT INACTIVE");
            println!("grant_id : {grant_id}");
            println!("status   : inactive");
            Ok(())
        }
    }
}

fn create_grant(conn: &Connection, spec: &GrantCreateSpec<'_>) -> DynResult<i64> {
'''
new = r'''        GrantCommand::Deactivate { db: path, grant_id } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if grant_id <= 0 {
                return Err("grant_id must be positive".into());
            }
            if !deactivate_grant(&conn, grant_id)? {
                return Err(format!("unknown delegated grant: {grant_id}").into());
            }
            println!("DELEGATED GRANT INACTIVE");
            println!("grant_id : {grant_id}");
            println!("status   : inactive");
            Ok(())
        }
        GrantCommand::Durable { command } => dispatch_durable(command),
    }
}

fn dispatch_durable(command: DurableGrantCommand) -> DynResult {
    match command {
        DurableGrantCommand::Create {
            db: path,
            principal_provider,
            principal_subject,
            participant_id,
            capability,
            resource,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let spec = DurableGrantCreateSpec {
                principal_provider: &principal_provider,
                principal_subject: &principal_subject,
                participant_id: &participant_id,
                capability: &capability,
                resource: resource.as_deref(),
            };
            let outcome = create_durable_grant(&conn, &spec)?;
            let state = match outcome.state {
                DurableGrantCreateState::Created => "created",
                DurableGrantCreateState::Existing => "existing",
                DurableGrantCreateState::Reactivated => "reactivated",
            };
            println!("DURABLE PRINCIPAL GRANT READY");
            println!("grant_id           : {}", outcome.id);
            println!("state              : {state}");
            println!("principal_provider : {}", principal_provider.trim());
            println!("principal_subject  : {}", principal_subject.trim());
            println!("participant_id     : {}", participant_id.trim());
            println!("capability         : {}", capability.trim());
            println!(
                "resource           : {}",
                resource.as_deref().unwrap_or("*")
            );
            Ok(())
        }
        DurableGrantCommand::List {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let grants = list_durable_grants(&conn, participant_id.as_deref())?;
            println!("DURABLE PRINCIPAL GRANTS");
            println!("id\tprincipal\tparticipant_id\tcapability\tresource\tstatus\tcreated_at\tupdated_at");
            for grant in grants {
                println!(
                    "{}\t{}:{}\t{}\t{}\t{}\t{}\t{}\t{}",
                    grant.id,
                    grant.principal_provider,
                    grant.principal_subject,
                    grant.participant_id,
                    grant.capability,
                    grant.resource.as_deref().unwrap_or("*"),
                    grant.status,
                    grant.created_at,
                    grant.updated_at,
                );
            }
            Ok(())
        }
        DurableGrantCommand::Deactivate { db: path, grant_id } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if grant_id <= 0 {
                return Err("grant_id must be positive".into());
            }
            let Some(updated_rows) = deactivate_durable_grant(&conn, grant_id)? else {
                return Err(format!("unknown durable principal grant: {grant_id}").into());
            };
            println!("DURABLE PRINCIPAL GRANT INACTIVE");
            println!("grant_id      : {grant_id}");
            println!("status        : inactive");
            println!("rows_changed  : {updated_rows}");
            Ok(())
        }
    }
}

fn create_durable_grant(
    conn: &Connection,
    spec: &DurableGrantCreateSpec<'_>,
) -> DynResult<DurableGrantCreateOutcome> {
    authorization::ensure_grant_schema(conn)?;
    let principal_provider = normalize(
        spec.principal_provider,
        MAX_PROVIDER_BYTES,
        "principal_provider",
    )?;
    let principal_subject = normalize(
        spec.principal_subject,
        MAX_SUBJECT_BYTES,
        "principal_subject",
    )?;
    let participant_id =
        identity::validate_participant_id(spec.participant_id).ok_or("invalid participant_id")?;
    require_active_participant(conn, &participant_id)?;
    let capability = normalize(spec.capability, MAX_CAPABILITY_BYTES, "capability")?;
    if !authorization::is_known_capability(&capability) {
        return Err(format!("unsupported capability: {capability}").into());
    }
    let resource = normalize_optional(spec.resource, MAX_RESOURCE_BYTES, "resource")?;

    let mut stmt = conn.prepare(
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

    if let Some((id, _)) = rows.iter().find(|(_, status)| status == "active") {
        return Ok(DurableGrantCreateOutcome {
            id: *id,
            state: DurableGrantCreateState::Existing,
        });
    }
    if let Some((id, _)) = rows.first() {
        conn.execute(
            "UPDATE principal_grants
             SET status = 'active', updated_at = unixepoch()
             WHERE id = ?1",
            [id],
        )?;
        return Ok(DurableGrantCreateOutcome {
            id: *id,
            state: DurableGrantCreateState::Reactivated,
        });
    }

    conn.execute(
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
    Ok(DurableGrantCreateOutcome {
        id: conn.last_insert_rowid(),
        state: DurableGrantCreateState::Created,
    })
}

fn list_durable_grants(
    conn: &Connection,
    participant_id: Option<&str>,
) -> rusqlite::Result<Vec<DurableGrantInspection>> {
    authorization::ensure_grant_schema(conn)?;
    let mut query = String::from(
        "SELECT id, principal_provider, principal_subject, participant_id, capability,
                resource, status, created_at, updated_at
         FROM principal_grants",
    );
    if participant_id.is_some() {
        query.push_str(" WHERE participant_id = ?1");
    }
    query.push_str(" ORDER BY id");

    let mut stmt = conn.prepare(&query)?;
    let map_row = |row: &rusqlite::Row<'_>| {
        Ok(DurableGrantInspection {
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
    if let Some(participant_id) = participant_id {
        stmt.query_map([participant_id], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>()
    } else {
        stmt.query_map([], map_row)?
            .collect::<rusqlite::Result<Vec<_>>>()
    }
}

fn deactivate_durable_grant(
    conn: &Connection,
    grant_id: i64,
) -> rusqlite::Result<Option<usize>> {
    authorization::ensure_grant_schema(conn)?;
    let scope: Option<(String, String, String, String, Option<String>)> = conn
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
        return Ok(None);
    };
    let updated = conn.execute(
        "UPDATE principal_grants
         SET status = 'inactive', updated_at = unixepoch()
         WHERE principal_provider = ?1
           AND principal_subject = ?2
           AND participant_id = ?3
           AND capability = ?4
           AND resource IS ?5
           AND status != 'inactive'",
        params![provider, subject, participant_id, capability, resource],
    )?;
    Ok(Some(updated))
}

fn create_grant(conn: &Connection, spec: &GrantCreateSpec<'_>) -> DynResult<i64> {
'''
s = replace_once(s, old, new, "durable grant dispatch and helpers")

old = '''    let participant_status: Option<String> = conn
        .query_row(
            "SELECT status FROM web_participants WHERE participant_id = ?1",
            [&participant_id],
            |row| row.get(0),
        )
        .optional()?;
    match participant_status.as_deref() {
        Some("active") => {}
        Some(_) => return Err(format!("participant is not active: {participant_id}").into()),
        None => return Err(format!("unknown participant: {participant_id}").into()),
    }

    if let Some(expires_at) = spec.expires_at {
'''
new = '''    require_active_participant(conn, &participant_id)?;

    if let Some(expires_at) = spec.expires_at {
'''
s = replace_once(s, old, new, "shared participant lifecycle helper")

anchor = '''fn normalize(value: &str, max_bytes: usize, field: &'static str) -> DynResult<String> {
'''
insert = r'''fn require_active_participant(conn: &Connection, participant_id: &str) -> DynResult {
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

fn normalize(value: &str, max_bytes: usize, field: &'static str) -> DynResult<String> {
'''
s = replace_once(s, anchor, insert, "active participant helper")

anchor = '''    #[test]
    fn create_list_and_deactivate_round_trip() {
'''
tests = r'''    #[test]
    fn durable_create_is_idempotent_and_reactivates_same_scope() {
        let (_dir, conn) = setup();
        let spec = DurableGrantCreateSpec {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: Some("control-systems"),
        };
        let created = create_durable_grant(&conn, &spec).unwrap();
        assert_eq!(created.state, DurableGrantCreateState::Created);
        let existing = create_durable_grant(&conn, &spec).unwrap();
        assert_eq!(existing.id, created.id);
        assert_eq!(existing.state, DurableGrantCreateState::Existing);
        assert_eq!(list_durable_grants(&conn, Some("maker-main")).unwrap().len(), 1);

        assert_eq!(deactivate_durable_grant(&conn, created.id).unwrap(), Some(1));
        assert_eq!(deactivate_durable_grant(&conn, created.id).unwrap(), Some(0));
        let reactivated = create_durable_grant(&conn, &spec).unwrap();
        assert_eq!(reactivated.id, created.id);
        assert_eq!(reactivated.state, DurableGrantCreateState::Reactivated);
        let grant = list_durable_grants(&conn, Some("maker-main"))
            .unwrap()
            .remove(0);
        assert_eq!(grant.status, "active");
    }

    #[test]
    fn durable_create_rejects_unknown_inactive_and_unsupported_capability() {
        let (_dir, conn) = setup();
        let unknown = DurableGrantCreateSpec {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "missing-main",
            capability: authorization::POST_MESSAGE,
            resource: None,
        };
        assert!(create_durable_grant(&conn, &unknown).is_err());

        identity::set_web_participant_status(&conn, "maker-main", "inactive").unwrap();
        let inactive = DurableGrantCreateSpec {
            participant_id: "maker-main",
            ..unknown.clone()
        };
        assert!(create_durable_grant(&conn, &inactive).is_err());
        identity::set_web_participant_status(&conn, "maker-main", "active").unwrap();

        let unsupported = DurableGrantCreateSpec {
            capability: "invented_capability",
            ..inactive
        };
        assert!(create_durable_grant(&conn, &unsupported).is_err());
    }

    #[test]
    fn durable_resource_scope_remains_authoritative_over_implicit_hmac_fallback() {
        let (_dir, conn) = setup();
        let spec = DurableGrantCreateSpec {
            principal_provider: "participant-hmac",
            principal_subject: "maker-main",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: Some("blackboard-lounge"),
        };
        create_durable_grant(&conn, &spec).unwrap();
        let principal = Principal {
            provider: "participant-hmac".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let decision = authorization::evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            authorization::POST_MESSAGE,
            Some("control-systems"),
            None,
        )
        .unwrap();
        assert!(!decision.allowed);
        assert_eq!(decision.reason, "explicit_resource_scope_mismatch");
    }

    #[test]
    fn durable_deactivate_closes_legacy_duplicate_wildcard_scope() {
        let (_dir, conn) = setup();
        authorization::ensure_grant_schema(&conn).unwrap();
        for _ in 0..2 {
            conn.execute(
                "INSERT INTO principal_grants
                    (principal_provider, principal_subject, participant_id, capability, resource)
                 VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message', NULL)",
                [],
            )
            .unwrap();
        }
        let grants = list_durable_grants(&conn, Some("maker-main")).unwrap();
        assert_eq!(grants.len(), 2);
        assert_eq!(deactivate_durable_grant(&conn, grants[0].id).unwrap(), Some(2));
        let grants = list_durable_grants(&conn, Some("maker-main")).unwrap();
        assert!(grants.iter().all(|grant| grant.status == "inactive"));
    }

    #[test]
    fn create_list_and_deactivate_round_trip() {
'''
s = replace_once(s, anchor, tests, "durable grant tests")
p.write_text(s, encoding="utf-8")


# main.rs: parser regression for the nested durable namespace.
p = Path("src/main.rs")
s = p.read_text(encoding="utf-8")
anchor = '''    #[test]
    fn execution_audit_cli_parses() {
'''
test = r'''    #[test]
    fn durable_principal_grant_cli_parses() {
        let create = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "durable",
            "create",
            "--db",
            "board.db",
            "--principal-provider",
            "oidc:https://issuer.example",
            "--principal-subject",
            "agent-1",
            "--participant-id",
            "maker-main",
            "--capability",
            "post_message",
            "--resource",
            "control-systems",
        ])
        .unwrap();
        assert!(matches!(
            create.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::Durable {
                    command: grant_admin::DurableGrantCommand::Create { .. }
                }
            })
        ));

        let list = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "durable",
            "list",
            "--db",
            "board.db",
            "--participant-id",
            "maker-main",
        ])
        .unwrap();
        assert!(matches!(
            list.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::Durable {
                    command: grant_admin::DurableGrantCommand::List { .. }
                }
            })
        ));

        let deactivate = Cli::try_parse_from([
            "conversation-blackboard",
            "grant",
            "durable",
            "deactivate",
            "--db",
            "board.db",
            "--grant-id",
            "7",
        ])
        .unwrap();
        assert!(matches!(
            deactivate.command,
            Some(Command::Grant {
                command: grant_admin::GrantCommand::Durable {
                    command: grant_admin::DurableGrantCommand::Deactivate { grant_id: 7, .. }
                }
            })
        ));
    }

    #[test]
    fn execution_audit_cli_parses() {
'''
s = replace_once(s, anchor, test, "durable grant CLI parser test")
p.write_text(s, encoding="utf-8")


# operations.md: document durable vs delegated operator semantics.
p = Path("docs/operations.md")
s = p.read_text(encoding="utf-8")
anchor = '''## GitHub participant ownership
'''
section = r'''## Authorization grants

Authentication credentials and authorization grants are separate operator objects. A grant stores principal and scope metadata only; it never stores bearer/JWT/HMAC/TOTP credential material.

Use **durable principal grants** for stable scoped authority:

```powershell
.\conversation-blackboard.exe grant durable create `
  --db <DB> `
  --principal-provider <PROVIDER> `
  --principal-subject <SUBJECT> `
  --participant-id <ID> `
  --capability <CAPABILITY> `
  --resource <RESOURCE>

.\conversation-blackboard.exe grant durable list --db <DB> --participant-id <ID>
.\conversation-blackboard.exe grant durable deactivate --db <DB> --grant-id <GRANT_ID>
```

Omit `--resource` only when wildcard resource authority is intentionally required. Durable create is idempotent for an already-active exact scope and reactivates the same authority object after deactivation.

A durable explicit grant is authoritative for its principal/participant/capability scope. Once explicit grants exist for that tuple, an implicit compatibility rule must not widen access to a different resource. Use `grant explain` before and after changes when scope effects are not obvious.

Use the existing **delegated grants** for narrower authority with optional expiry, semantic intent binding, and one-shot consumption:

```powershell
.\conversation-blackboard.exe grant create ...
.\conversation-blackboard.exe grant list --db <DB> --participant-id <ID>
.\conversation-blackboard.exe grant explain ...
.\conversation-blackboard.exe grant deactivate --db <DB> --grant-id <GRANT_ID>
```

Delegated one-shot consumption remains part of the same semantic execution transaction as the committed effect and receipt. Failed semantic execution does not burn one-shot authority.

## GitHub participant ownership
'''
s = replace_once(s, anchor, section, "operations grant documentation")
p.write_text(s, encoding="utf-8")
