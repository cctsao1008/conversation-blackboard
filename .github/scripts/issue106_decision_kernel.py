from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# authorization.rs: dedicated capability/resource, schema boundary, and shared kernel.
p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")

old = '''pub const READ_AUTHORIZATION_POLICY: &str = "read_authorization_policy";\npub const AUTHORIZATION_POLICY_RESOURCE: &str = "authorization-policy";\npub const MANAGE_CHANNELS: &str = "manage_channels";\n\nconst KNOWN_CAPABILITIES: [&str; 9] = ['''
new = '''pub const READ_AUTHORIZATION_POLICY: &str = "read_authorization_policy";\npub const AUTHORIZATION_POLICY_RESOURCE: &str = "authorization-policy";\npub const READ_AUTHORIZATION_DECISION: &str = "read_authorization_decision";\npub const AUTHORIZATION_DECISION_RESOURCE: &str = "authorization-decision";\npub const MANAGE_CHANNELS: &str = "manage_channels";\n\nconst KNOWN_CAPABILITIES: [&str; 10] = ['''
s = replace_once(s, old, new, "decision capability constants")

old = '''    READ_AUTHORIZATION_POLICY_INTEGRITY,\n    READ_AUTHORIZATION_POLICY,\n    MANAGE_CHANNELS,'''
new = '''    READ_AUTHORIZATION_POLICY_INTEGRITY,\n    READ_AUTHORIZATION_POLICY,\n    READ_AUTHORIZATION_DECISION,\n    MANAGE_CHANNELS,'''
s = replace_once(s, old, new, "known capability list")

old = '''pub fn evaluate_authorization(\n    conn: &Connection,\n    principal: &Principal,\n    participant_id: &str,\n    capability: &str,\n    resource: Option<&str>,\n    intent_id: Option<&str>,\n) -> rusqlite::Result<AuthorizationDecision> {\n    ensure_grant_schema(conn)?;\n    let Some(participant) = participant_policy_row(conn, participant_id)? else {'''
new = '''pub fn authorization_decision_schema_current(conn: &Connection) -> rusqlite::Result<bool> {\n    Ok(table_has_columns(\n        conn,\n        "principal_grants",\n        &[\n            "id",\n            "principal_provider",\n            "principal_subject",\n            "participant_id",\n            "capability",\n            "resource",\n            "status",\n        ],\n    )? && table_has_columns(\n        conn,\n        "delegated_grants",\n        &[\n            "id",\n            "principal_provider",\n            "principal_subject",\n            "participant_id",\n            "capability",\n            "resource",\n            "intent_id",\n            "expires_at",\n            "one_shot",\n            "consumed_at",\n            "consumed_intent_id",\n            "status",\n        ],\n    )? && table_has_columns(\n        conn,\n        "web_participants",\n        &[\n            "participant_id",\n            "status",\n            "role",\n            "owner_provider",\n            "owner_subject",\n        ],\n    )? && table_has_columns(\n        conn,\n        "execution_receipts",\n        &["participant_id", "intent_id", "status"],\n    )?)\n}\n\npub fn explain_authorization(\n    conn: &Connection,\n    principal: &Principal,\n    participant_id: &str,\n    capability: &str,\n    resource: Option<&str>,\n    intent_id: Option<&str>,\n) -> rusqlite::Result<AuthorizationDecision> {\n    if !authorization_decision_schema_current(conn)? {\n        return Err(rusqlite::Error::InvalidQuery);\n    }\n    evaluate_authorization_current_schema(\n        conn,\n        principal,\n        participant_id,\n        capability,\n        resource,\n        intent_id,\n    )\n}\n\npub fn evaluate_authorization(\n    conn: &Connection,\n    principal: &Principal,\n    participant_id: &str,\n    capability: &str,\n    resource: Option<&str>,\n    intent_id: Option<&str>,\n) -> rusqlite::Result<AuthorizationDecision> {\n    ensure_grant_schema(conn)?;\n    evaluate_authorization_current_schema(\n        conn,\n        principal,\n        participant_id,\n        capability,\n        resource,\n        intent_id,\n    )\n}\n\nfn evaluate_authorization_current_schema(\n    conn: &Connection,\n    principal: &Principal,\n    participant_id: &str,\n    capability: &str,\n    resource: Option<&str>,\n    intent_id: Option<&str>,\n) -> rusqlite::Result<AuthorizationDecision> {\n    let Some(participant) = participant_policy_row(conn, participant_id)? else {'''
s = replace_once(s, old, new, "shared decision kernel")

old = '''            READ_AUTHORIZATION_POLICY_INTEGRITY => Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),\n            READ_AUTHORIZATION_POLICY => Some(AUTHORIZATION_POLICY_RESOURCE),\n            _ => None,'''
new = '''            READ_AUTHORIZATION_POLICY_INTEGRITY => Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),\n            READ_AUTHORIZATION_POLICY => Some(AUTHORIZATION_POLICY_RESOURCE),\n            READ_AUTHORIZATION_DECISION => Some(AUTHORIZATION_DECISION_RESOURCE),\n            _ => None,'''
s = replace_once(s, old, new, "effective grants resource")

old = '''        if capability == READ_AUTHORIZATION_POLICY_INTEGRITY {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE))\n            .then_some("implicit_human_web_admin_authorization_policy_integrity");\n        }\n        if capability == READ_EXECUTION_AUDIT_SWEEP {'''
new = '''        if capability == READ_AUTHORIZATION_POLICY_INTEGRITY {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE))\n            .then_some("implicit_human_web_admin_authorization_policy_integrity");\n        }\n        if capability == READ_AUTHORIZATION_DECISION {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(AUTHORIZATION_DECISION_RESOURCE))\n            .then_some("implicit_human_web_admin_authorization_decision");\n        }\n        if capability == READ_EXECUTION_AUDIT_SWEEP {'''
s = replace_once(s, old, new, "implicit decision authority")

# Insert focused domain tests after setup().
marker = '''    #[test]\n    fn authorization_policy_snapshot_reads_full_lifecycle_without_filtering() {'''
insert = r'''    #[test]
    fn authorization_decision_explain_refuses_legacy_schema_without_mutation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("legacy.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                owner_provider TEXT,
                owner_subject TEXT
            );
            INSERT INTO web_participants (participant_id, status, role)
            VALUES ('maker-main', 'active', 'admin');",
        )
        .unwrap();
        let before: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let principal = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "agent-1".to_owned(),
        };
        assert!(!authorization_decision_schema_current(&conn).unwrap());
        assert!(explain_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            None,
        )
        .is_err());
        let after: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before, after);
        let grant_tables: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'table' AND name IN ('principal_grants', 'delegated_grants')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(grant_tables, 0);
    }

    #[test]
    fn authorization_decision_explain_reuses_kernel_without_consuming_one_shot() {
        let (_dir, conn) = setup();
        conn.execute(
            "INSERT INTO delegated_grants
                (principal_provider, principal_subject, participant_id, capability, resource,
                 intent_id, expires_at, one_shot)
             VALUES ('oidc:https://issuer.example', 'agent-1', 'maker-main', 'post_message',
                     'control-systems', 'intent-106', unixepoch() + 3600, 1)",
            [],
        )
        .unwrap();
        let principal = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "agent-1".to_owned(),
        };
        let explained = explain_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            Some("intent-106"),
        )
        .unwrap();
        let executable = evaluate_authorization(
            &conn,
            &principal,
            "maker-main",
            POST_MESSAGE,
            Some("control-systems"),
            Some("intent-106"),
        )
        .unwrap();
        assert_eq!(explained, executable);
        assert!(explained.allowed);
        assert!(explained.consume_grant_id.is_some());
        let consumed_at: Option<i64> = conn
            .query_row(
                "SELECT consumed_at FROM delegated_grants WHERE intent_id = 'intent-106'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(consumed_at.is_none());
    }

    #[test]
    fn authorization_decision_implicit_authority_is_human_web_admin_only() {
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
            READ_AUTHORIZATION_DECISION,
            Some(AUTHORIZATION_DECISION_RESOURCE),
        )
        .unwrap());
        identity::set_web_participant_role(&conn, "maker-main", "admin").unwrap();
        assert!(authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_DECISION,
            Some(AUTHORIZATION_DECISION_RESOURCE),
        )
        .unwrap());
        for principal in [&hmac, &github, &oidc] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                READ_AUTHORIZATION_DECISION,
                Some(AUTHORIZATION_DECISION_RESOURCE),
            )
            .unwrap());
        }
    }

'''
if "authorization_decision_explain_refuses_legacy_schema_without_mutation" in s:
    raise SystemExit("decision tests already exist")
s = replace_once(s, marker, insert + marker, "decision domain tests")
p.write_text(s, encoding="utf-8")

# grant_admin.rs: local explain uses read-only connection + observational wrapper.
p = Path("src/grant_admin.rs")
s = p.read_text(encoding="utf-8")
old = '''        GrantCommand::Explain {\n            db: path,\n            principal_provider,\n            principal_subject,\n            participant_id,\n            capability,\n            resource,\n            intent_id,\n        } => {\n            require_database(&path)?;\n            let conn = db::connect(&path)?;'''
new = '''        GrantCommand::Explain {\n            db: path,\n            principal_provider,\n            principal_subject,\n            participant_id,\n            capability,\n            resource,\n            intent_id,\n        } => {\n            require_database(&path)?;\n            let conn = db::connect_read_only(&path)?;\n            if !authorization::authorization_decision_schema_current(&conn)? {\n                return Err(format!(\n                    "authorization decision schema requires migration: run `conversation-blackboard db init --db {}` before explain",\n                    path.display()\n                )\n                .into());\n            }'''
s = replace_once(s, old, new, "grant explain read-only connection")

old = '''            let explanation = authorization::evaluate_authorization(\n                &conn,\n                &principal,\n                &participant_id,\n                &capability,\n                resource.as_deref(),\n                intent_id.as_deref(),\n            )?;'''
new = '''            let explanation = authorization::explain_authorization(\n                &conn,\n                &principal,\n                &participant_id,\n                &capability,\n                resource.as_deref(),\n                intent_id.as_deref(),\n            )?;'''
s = replace_once(s, old, new, "grant explain wrapper")

# Existing explain regression tests should exercise the observational wrapper too.
s = s.replace(
    '''        let explanation = authorization::evaluate_authorization(\n            &conn,\n            &principal,''',
    '''        let explanation = authorization::explain_authorization(\n            &conn,\n            &principal,''',
)

marker = '''    #[test]\n    fn grant_verify_accepts_clean_database() {'''
insert = r'''    #[test]
    fn grant_explain_refuses_legacy_schema_without_mutation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("legacy-explain.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE web_participants (
                participant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                owner_provider TEXT,
                owner_subject TEXT
            );
            INSERT INTO web_participants (participant_id, status, role)
            VALUES ('maker-main', 'active', 'admin');",
        )
        .unwrap();
        let before: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        drop(conn);

        let error = dispatch(GrantCommand::Explain {
            db: path.clone(),
            principal_provider: "oidc:https://issuer.example".to_owned(),
            principal_subject: "agent-1".to_owned(),
            participant_id: "maker-main".to_owned(),
            capability: authorization::POST_MESSAGE.to_owned(),
            resource: Some("control-systems".to_owned()),
            intent_id: None,
        })
        .expect_err("legacy decision schema must be refused")
        .to_string();
        assert!(error.contains("authorization decision schema requires migration"));

        let conn = Connection::open(&path).unwrap();
        let after: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'index')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before, after);
        let grant_tables: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'table' AND name IN ('principal_grants', 'delegated_grants')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(grant_tables, 0);
    }

'''
if "grant_explain_refuses_legacy_schema_without_mutation" in s:
    raise SystemExit("grant explain no-mutation test already exists")
s = replace_once(s, marker, insert + marker, "grant explain no-mutation test")
p.write_text(s, encoding="utf-8")
