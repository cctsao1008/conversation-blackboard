from pathlib import Path

AUTH = Path('src/authorization.rs')
ADMIN = Path('src/authorization_admin.rs')

auth = AUTH.read_text()

old = '''pub const READ_AUTHORIZATION_DECISION: &str = "read_authorization_decision";\npub const AUTHORIZATION_DECISION_RESOURCE: &str = "authorization-decision";\npub const MANAGE_CHANNELS: &str = "manage_channels";'''
new = '''pub const READ_AUTHORIZATION_DECISION: &str = "read_authorization_decision";\npub const AUTHORIZATION_DECISION_RESOURCE: &str = "authorization-decision";\npub const MANAGE_AUTHORIZATION_POLICY: &str = "manage_authorization_policy";\npub const AUTHORIZATION_POLICY_ADMIN_RESOURCE: &str = "authorization-policy-administration";\npub const MANAGE_CHANNELS: &str = "manage_channels";'''
assert old in auth
auth = auth.replace(old, new, 1)

auth = auth.replace('const KNOWN_CAPABILITIES: [&str; 10] = [', 'const KNOWN_CAPABILITIES: [&str; 11] = [', 1)
old = '''    READ_AUTHORIZATION_DECISION,\n    MANAGE_CHANNELS,'''
new = '''    READ_AUTHORIZATION_DECISION,\n    MANAGE_AUTHORIZATION_POLICY,\n    MANAGE_CHANNELS,'''
assert old in auth
auth = auth.replace(old, new, 1)

old = '''            READ_AUTHORIZATION_DECISION => Some(AUTHORIZATION_DECISION_RESOURCE),\n            _ => None,'''
new = '''            READ_AUTHORIZATION_DECISION => Some(AUTHORIZATION_DECISION_RESOURCE),\n            MANAGE_AUTHORIZATION_POLICY => Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),\n            _ => None,'''
assert old in auth
auth = auth.replace(old, new, 1)

old = '''        if capability == READ_EXECUTION_AUDIT_SWEEP {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(EXECUTION_AUDIT_SWEEP_RESOURCE))\n            .then_some("implicit_human_web_admin_audit_sweep");\n        }'''
new = '''        if capability == MANAGE_AUTHORIZATION_POLICY {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE))\n            .then_some("implicit_human_web_admin_authorization_administration");\n        }\n        if capability == READ_EXECUTION_AUDIT_SWEEP {\n            return (principal.provider == "human-web"\n                && participant.role == "admin"\n                && resource == Some(EXECUTION_AUDIT_SWEEP_RESOURCE))\n            .then_some("implicit_human_web_admin_audit_sweep");\n        }'''
assert old in auth
auth = auth.replace(old, new, 1)

marker = '''    #[test]\n    fn authorization_decision_implicit_authority_is_human_web_admin_only() {'''
assert marker in auth
test = r'''    #[test]
    fn authorization_administration_authority_is_human_web_admin_only_and_isolated() {
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
            subject: "admin-agent".to_owned(),
        };

        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
        identity::set_web_participant_role(&conn, "maker-main", "admin").unwrap();
        assert!(authorize(
            &conn,
            &human,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
        for principal in [&hmac, &github, &oidc] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                MANAGE_AUTHORIZATION_POLICY,
                Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
            )
            .unwrap());
        }

        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                &oidc.provider,
                &oidc.subject,
                MANAGE_AUTHORIZATION_POLICY,
                AUTHORIZATION_POLICY_ADMIN_RESOURCE
            ],
        )
        .unwrap();
        assert!(authorize(
            &conn,
            &oidc,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
        for (capability, resource) in [
            (READ_AUTHORIZATION_POLICY, Some(AUTHORIZATION_POLICY_RESOURCE)),
            (
                READ_AUTHORIZATION_POLICY_INTEGRITY,
                Some(AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
            ),
            (READ_AUTHORIZATION_DECISION, Some(AUTHORIZATION_DECISION_RESOURCE)),
            (
                READ_EXECUTION_AUDIT_SWEEP,
                Some(EXECUTION_AUDIT_SWEEP_RESOURCE),
            ),
            (MANAGE_CHANNELS, None),
        ] {
            assert!(!authorize(&conn, &oidc, "maker-main", capability, resource).unwrap());
        }
    }

'''
auth = auth.replace(marker, test + marker, 1)
AUTH.write_text(auth)

admin = ADMIN.read_text()

marker = '''#[derive(Debug)]\nstruct DelegatedGrantState {\n    principal_provider: String,\n    principal_subject: String,\n    participant_id: String,\n    capability: String,\n    resource: Option<String>,\n    intent_id: Option<String>,\n    expires_at: Option<i64>,\n    one_shot: bool,\n    status: String,\n}\n'''
assert marker in admin
insert = marker + r'''
#[derive(Debug)]
struct NormalizedDurableGrantCreate {
    principal_provider: String,
    principal_subject: String,
    participant_id: String,
    capability: String,
    resource: Option<String>,
}
'''
admin = admin.replace(marker, insert, 1)

start = admin.index('pub fn create_durable_grant(\n')
end = admin.index('pub fn deactivate_durable_grant(\n', start)
replacement = r'''fn normalize_durable_grant_create_request(
    request: &DurableGrantCreateRequest<'_>,
) -> AdministrationResult<NormalizedDurableGrantCreate> {
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
    Ok(NormalizedDurableGrantCreate {
        principal_provider,
        principal_subject,
        participant_id,
        capability,
        resource,
    })
}

fn create_durable_grant_in_tx(
    tx: &Transaction<'_>,
    actor: &NormalizedActor,
    request: &NormalizedDurableGrantCreate,
) -> AdministrationResult<DurableGrantCreateOutcome> {
    require_active_participant(tx, &request.participant_id)?;
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
                    &request.principal_provider,
                    &request.principal_subject,
                    &request.participant_id,
                    &request.capability,
                    request.resource.as_deref(),
                ],
                |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows
    };

    if let Some((id, _)) = rows.iter().find(|(_, status)| status == "active") {
        return Ok(DurableGrantCreateOutcome {
            id: *id,
            state: DurableGrantCreateState::Existing,
        });
    }

    let scope = GrantScope {
        principal_provider: &request.principal_provider,
        principal_subject: &request.principal_subject,
        participant_id: &request.participant_id,
        capability: &request.capability,
        resource: request.resource.as_deref(),
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
            tx,
            actor,
            &AdministrationEvent {
                grant_store: "durable",
                grant_id: *id,
                operation: "reactivate",
                scope,
                before_status: Some("inactive"),
                after_status: "active",
            },
        )?;
        return Ok(DurableGrantCreateOutcome {
            id: *id,
            state: DurableGrantCreateState::Reactivated,
        });
    }

    tx.execute(
        "INSERT INTO principal_grants
            (principal_provider, principal_subject, participant_id, capability, resource)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        params![
            &request.principal_provider,
            &request.principal_subject,
            &request.participant_id,
            &request.capability,
            request.resource.as_deref(),
        ],
    )?;
    let id = tx.last_insert_rowid();
    record_event(
        tx,
        actor,
        &AdministrationEvent {
            grant_store: "durable",
            grant_id: id,
            operation: "create",
            scope,
            before_status: None,
            after_status: "active",
        },
    )?;
    Ok(DurableGrantCreateOutcome {
        id,
        state: DurableGrantCreateState::Created,
    })
}

pub fn create_durable_grant(
    conn: &Connection,
    actor: &AuthorizationAdministrationActor<'_>,
    request: &DurableGrantCreateRequest<'_>,
) -> AdministrationResult<DurableGrantCreateOutcome> {
    require_schema_current(conn)?;
    let actor = normalize_actor(actor)?;
    let request = normalize_durable_grant_create_request(request)?;
    let tx = conn.unchecked_transaction()?;
    let outcome = create_durable_grant_in_tx(&tx, &actor, &request)?;
    tx.commit()?;
    Ok(outcome)
}

#[cfg(test)]
pub fn create_durable_grant_authorized(
    conn: &Connection,
    surface: &str,
    caller_principal: &Principal,
    caller_participant_id: &str,
    request: &DurableGrantCreateRequest<'_>,
) -> AdministrationResult<DurableGrantCreateOutcome> {
    require_schema_current(conn)?;
    let actor_spec = AuthorizationAdministrationActor {
        surface,
        principal: Some(caller_principal),
        participant_id: Some(caller_participant_id),
    };
    let actor = normalize_actor(&actor_spec)?;
    let request = normalize_durable_grant_create_request(request)?;
    let tx = conn.unchecked_transaction()?;
    let decision = authorization::explain_authorization(
        &tx,
        caller_principal,
        caller_participant_id,
        authorization::MANAGE_AUTHORIZATION_POLICY,
        Some(authorization::AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        None,
    )?;
    if !decision.allowed {
        return Err("authorization denied".into());
    }
    let outcome = create_durable_grant_in_tx(&tx, &actor, &request)?;
    tx.commit()?;
    Ok(outcome)
}

'''
admin = admin[:start] + replacement + admin[end:]

marker = '''    #[test]\n    fn delegated_lifecycle_records_scope_and_authenticated_actor_without_credentials() {'''
assert marker in admin
test = r'''    #[test]
    fn authorized_durable_create_checks_authority_and_mutates_in_one_boundary() {
        let (_dir, conn) = setup();
        identity::provision_web_participant_identity(
            &conn,
            "admin-main",
            "admin",
            Some("Administrator"),
        )
        .unwrap()
        .unwrap();
        let admin = Principal {
            provider: "human-web".to_owned(),
            subject: "admin-main".to_owned(),
        };
        let request = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "agent-1",
            participant_id: "maker-main",
            capability: authorization::POST_MESSAGE,
            resource: Some("control-systems"),
        };

        let created = create_durable_grant_authorized(
            &conn,
            "rest-test",
            &admin,
            "admin-main",
            &request,
        )
        .unwrap();
        assert_eq!(created.state, DurableGrantCreateState::Created);
        let event: (String, String, String, String) = conn
            .query_row(
                "SELECT actor_surface, actor_provider, actor_subject, actor_participant_id
                 FROM authorization_admin_events
                 WHERE grant_store = 'durable' AND grant_id = ?1",
                [created.id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .unwrap();
        assert_eq!(event.0, "rest-test");
        assert_eq!(event.1, "human-web");
        assert_eq!(event.2, "admin-main");
        assert_eq!(event.3, "admin-main");

        let ordinary = Principal {
            provider: "human-web".to_owned(),
            subject: "maker-main".to_owned(),
        };
        let denied = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "denied-agent",
            participant_id: "maker-main",
            capability: authorization::READ_MESSAGES,
            resource: None,
        };
        let before_events = event_count(&conn);
        assert!(create_durable_grant_authorized(
            &conn,
            "rest-test",
            &ordinary,
            "maker-main",
            &denied,
        )
        .is_err());
        assert_eq!(event_count(&conn), before_events);
        let denied_rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM principal_grants
                 WHERE principal_subject = 'denied-agent'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(denied_rows, 0);
    }

    #[test]
    fn explicit_remote_administration_grant_allows_external_principal() {
        let (_dir, conn) = setup();
        identity::provision_web_participant_identity(
            &conn,
            "admin-main",
            "admin",
            Some("Administrator"),
        )
        .unwrap()
        .unwrap();
        let local = AuthorizationAdministrationActor::local_cli();
        let bootstrap = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "admin-agent",
            participant_id: "admin-main",
            capability: authorization::MANAGE_AUTHORIZATION_POLICY,
            resource: Some(authorization::AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        };
        create_durable_grant(&conn, &local, &bootstrap).unwrap();

        let external = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "admin-agent".to_owned(),
        };
        let request = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "worker-agent",
            participant_id: "maker-main",
            capability: authorization::READ_MESSAGES,
            resource: None,
        };
        let created = create_durable_grant_authorized(
            &conn,
            "rest-test",
            &external,
            "admin-main",
            &request,
        )
        .unwrap();
        assert_eq!(created.state, DurableGrantCreateState::Created);
        let actor_provider: String = conn
            .query_row(
                "SELECT actor_provider FROM authorization_admin_events
                 WHERE grant_store = 'durable' AND grant_id = ?1",
                [created.id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(actor_provider, "oidc:https://issuer.example");
    }

'''
admin = admin.replace(marker, test + marker, 1)
ADMIN.write_text(admin)
