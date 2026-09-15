from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")

s = replace_once(
    s,
    '''pub const READ_AUTHORIZATION_POLICY_INTEGRITY: &str = "read_authorization_policy_integrity";\npub const AUTHORIZATION_POLICY_INTEGRITY_RESOURCE: &str = "authorization-policy-integrity";\npub const MANAGE_CHANNELS: &str = "manage_channels";\n\nconst KNOWN_CAPABILITIES: [&str; 8] = [\n    READ_MESSAGES,\n    POST_MESSAGE,\n    REPLY,\n    READ_EXECUTION_RECEIPT,\n    READ_EXECUTION_AUDIT,\n    READ_EXECUTION_AUDIT_SWEEP,\n    READ_AUTHORIZATION_POLICY_INTEGRITY,\n    MANAGE_CHANNELS,\n];''',
    '''pub const READ_AUTHORIZATION_POLICY_INTEGRITY: &str = "read_authorization_policy_integrity";\npub const AUTHORIZATION_POLICY_INTEGRITY_RESOURCE: &str = "authorization-policy-integrity";\npub const READ_AUTHORIZATION_POLICY: &str = "read_authorization_policy";\npub const AUTHORIZATION_POLICY_RESOURCE: &str = "authorization-policy";\npub const MANAGE_CHANNELS: &str = "manage_channels";\n\nconst KNOWN_CAPABILITIES: [&str; 9] = [\n    READ_MESSAGES,\n    POST_MESSAGE,\n    REPLY,\n    READ_EXECUTION_RECEIPT,\n    READ_EXECUTION_AUDIT,\n    READ_EXECUTION_AUDIT_SWEEP,\n    READ_AUTHORIZATION_POLICY_INTEGRITY,\n    READ_AUTHORIZATION_POLICY,\n    MANAGE_CHANNELS,\n];''',
    "policy snapshot capability constants",
)

s = replace_once(
    s,
    '''#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct AuthorizationIntegrityReport {\n    pub valid: bool,\n    pub durable_grants_scanned: usize,\n    pub delegated_grants_scanned: usize,\n    pub violations: Vec<AuthorizationIntegrityViolation>,\n}\n\n#[derive(Debug, Clone)]\nstruct ParticipantPolicyRow {''',
    '''#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct AuthorizationIntegrityReport {\n    pub valid: bool,\n    pub durable_grants_scanned: usize,\n    pub delegated_grants_scanned: usize,\n    pub violations: Vec<AuthorizationIntegrityViolation>,\n}\n\n#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct DurableGrantSnapshot {\n    pub id: i64,\n    pub principal_provider: String,\n    pub principal_subject: String,\n    pub participant_id: String,\n    pub capability: String,\n    pub resource: Option<String>,\n    pub status: String,\n    pub created_at: i64,\n    pub updated_at: i64,\n}\n\n#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct DelegatedGrantSnapshot {\n    pub id: i64,\n    pub principal_provider: String,\n    pub principal_subject: String,\n    pub participant_id: String,\n    pub capability: String,\n    pub resource: Option<String>,\n    pub intent_id: Option<String>,\n    pub expires_at: Option<i64>,\n    pub one_shot: bool,\n    pub consumed_at: Option<i64>,\n    pub consumed_intent_id: Option<String>,\n    pub status: String,\n    pub created_at: i64,\n    pub updated_at: i64,\n}\n\n#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct AuthorizationPolicySnapshot {\n    pub durable_grants: Vec<DurableGrantSnapshot>,\n    pub delegated_grants: Vec<DelegatedGrantSnapshot>,\n}\n\n#[derive(Debug, Clone)]\nstruct ParticipantPolicyRow {''',
    "policy snapshot domain structs",
)

reader = r'''pub fn authorization_policy_snapshot_schema_current(conn: &Connection) -> rusqlite::Result<bool> {
    Ok(table_has_columns(
        conn,
        "principal_grants",
        &[
            "id",
            "principal_provider",
            "principal_subject",
            "participant_id",
            "capability",
            "resource",
            "status",
            "created_at",
            "updated_at",
        ],
    )? && table_has_columns(
        conn,
        "delegated_grants",
        &[
            "id",
            "principal_provider",
            "principal_subject",
            "participant_id",
            "capability",
            "resource",
            "intent_id",
            "expires_at",
            "one_shot",
            "consumed_at",
            "consumed_intent_id",
            "status",
            "created_at",
            "updated_at",
        ],
    )?)
}

pub fn read_authorization_policy_snapshot(
    conn: &Connection,
) -> rusqlite::Result<AuthorizationPolicySnapshot> {
    if !authorization_policy_snapshot_schema_current(conn)? {
        return Err(rusqlite::Error::InvalidQuery);
    }

    let durable_grants = {
        let mut stmt = conn.prepare(
            "SELECT id, principal_provider, principal_subject, participant_id, capability,
                    resource, status, created_at, updated_at
             FROM principal_grants
             ORDER BY id",
        )?;
        stmt.query_map([], |row| {
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
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };

    let delegated_grants = {
        let mut stmt = conn.prepare(
            "SELECT id, principal_provider, principal_subject, participant_id, capability,
                    resource, intent_id, expires_at, one_shot, consumed_at,
                    consumed_intent_id, status, created_at, updated_at
             FROM delegated_grants
             ORDER BY id",
        )?;
        stmt.query_map([], |row| {
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
                created_at: row.get(12)?,
                updated_at: row.get(13)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };

    Ok(AuthorizationPolicySnapshot {
        durable_grants,
        delegated_grants,
    })
}

'''
s = replace_once(
    s,
    'pub fn authorization_integrity_schema_current(conn: &Connection) -> rusqlite::Result<bool> {',
    reader + 'pub fn authorization_integrity_schema_current(conn: &Connection) -> rusqlite::Result<bool> {',
    "policy snapshot read-only reader",
)

s = replace_once(
    s,
    '''        let implicit_resource = match capability {\n            READ_EXECUTION_AUDIT_SWEEP => Some(EXECUTION_AUDIT_SWEEP_RESOURCE),\n            READ_AUTHORIZATION_POLICY_INTEGRITY => Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),\n            _ => None,\n        };''',
    '''        let implicit_resource = match capability {\n            READ_EXECUTION_AUDIT_SWEEP => Some(EXECUTION_AUDIT_SWEEP_RESOURCE),\n            READ_AUTHORIZATION_POLICY_INTEGRITY => Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),\n            READ_AUTHORIZATION_POLICY => Some(AUTHORIZATION_POLICY_RESOURCE),\n            _ => None,\n        };''',
    "effective grant global resource",
)

s = replace_once(
    s,
    '''    if self_authenticated {\n        if capability == READ_AUTHORIZATION_POLICY_INTEGRITY {''',
    '''    if self_authenticated {\n        if capability == READ_AUTHORIZATION_POLICY {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(AUTHORIZATION_POLICY_RESOURCE))\n            .then_some("implicit_human_web_admin_authorization_policy");\n        }\n        if capability == READ_AUTHORIZATION_POLICY_INTEGRITY {''',
    "implicit policy snapshot authority",
)

test_anchor = '''    #[test]\n    fn authorization_integrity_accepts_clean_expiry_and_inactive_history() {'''
tests = r'''    #[test]
    fn authorization_policy_snapshot_reads_full_lifecycle_without_filtering() {
        let (_dir, conn) = setup();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource, status)
             VALUES ('oidc:https://issuer.example', 'active-agent', 'maker-main', 'post_message', 'alpha', 'active')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource, status)
             VALUES ('oidc:https://issuer.example', 'inactive-agent', 'maker-main', 'read_messages', NULL, 'inactive')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, expires_at, one_shot, status)
             VALUES ('oidc:https://issuer.example', 'expired-agent', 'maker-main', 'post_message',
                     'beta', 'expired-intent', unixepoch() - 60, 0, 'active')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, one_shot, consumed_at, consumed_intent_id, status)
             VALUES ('oidc:https://issuer.example', 'consumed-agent', 'maker-main', 'post_message',
                     'gamma', 'consumed-intent', 1, unixepoch(), 'consumed-intent', 'inactive')",
            [],
        )
        .unwrap();

        let snapshot = read_authorization_policy_snapshot(&conn).unwrap();
        assert_eq!(snapshot.durable_grants.len(), 2);
        assert_eq!(snapshot.delegated_grants.len(), 2);
        assert_eq!(snapshot.durable_grants[0].status, "active");
        assert_eq!(snapshot.durable_grants[1].status, "inactive");
        assert_eq!(snapshot.delegated_grants[0].intent_id.as_deref(), Some("expired-intent"));
        assert!(snapshot.delegated_grants[0].expires_at.is_some());
        assert!(snapshot.delegated_grants[1].one_shot);
        assert!(snapshot.delegated_grants[1].consumed_at.is_some());
        assert_eq!(
            snapshot.delegated_grants[1].consumed_intent_id.as_deref(),
            Some("consumed-intent")
        );
        assert_eq!(snapshot.delegated_grants[1].status, "inactive");
    }

    #[test]
    fn authorization_policy_snapshot_refuses_legacy_schema_without_mutation() {
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

        assert!(!authorization_policy_snapshot_schema_current(&conn).unwrap());
        assert!(read_authorization_policy_snapshot(&conn).is_err());
        let durable_has_created_at = table_has_columns(&conn, "principal_grants", &["created_at"])
            .unwrap();
        let delegated_has_updated_at = table_has_columns(&conn, "delegated_grants", &["updated_at"])
            .unwrap();
        assert!(!durable_has_created_at);
        assert!(!delegated_has_updated_at);
    }

    #[test]
    fn authorization_policy_snapshot_implicit_authority_is_human_admin_only() {
        let (_dir, conn) = setup();
        let human = Principal {
            provider: "human-web".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let hmac = Principal {
            provider: "participant-hmac".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let github = Principal {
            provider: "github".to_owned(),
            subject: "543608".to_owned(),
        };
        let oidc = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "agent-1".to_owned(),
        };

        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_RESOURCE),
        )
        .unwrap());
        identity::set_web_participant_role(&conn, "maker-main", "admin").unwrap();
        assert!(authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_RESOURCE),
        )
        .unwrap());
        for principal in [&hmac, &github, &oidc] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                READ_AUTHORIZATION_POLICY,
                Some(AUTHORIZATION_POLICY_RESOURCE),
            )
            .unwrap());
        }
    }

'''
s = replace_once(s, test_anchor, tests + test_anchor, "policy snapshot unit tests")

p.write_text(s, encoding="utf-8")
