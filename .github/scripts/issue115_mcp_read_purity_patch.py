from pathlib import Path

mcp_path = Path('src/mcp.rs')
mcp = mcp_path.read_text()


def replace_fn(text: str, name: str, next_name: str, body: str) -> str:
    start_marker = f'async fn {name}('
    end_marker = f'\nasync fn {next_name}('
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f'{name} start not found')
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f'{name} end not found')
    return text[:start] + body.rstrip() + '\n' + text[end + 1:]


# Credential lookup for capability HMAC proofs is itself observational.
start = mcp.find('async fn resolve_capability_identity(')
end = mcp.find('\nasync fn resolve_capability_principal(', start)
if start < 0 or end < 0:
    raise RuntimeError('capability identity boundary not found')
identity_block = mcp[start:end]
if 'with_db_read_only(state, move |conn|' not in identity_block:
    old = 'let auth = match with_db(state, move |conn| {'
    if old not in identity_block:
        raise RuntimeError('capability identity db lookup anchor not found')
    identity_block = identity_block.replace(
        old, 'let auth = match with_db_read_only(state, move |conn| {', 1
    )
    mcp = mcp[:start] + identity_block + mcp[end:]

# Principal resolution authenticates only. Policy evaluation belongs inside the
# schema-pure read-only handler boundary.
start = mcp.find('async fn resolve_capability_principal(')
end = mcp.find('\nasync fn blackboard_access_context(', start)
if start < 0 or end < 0:
    raise RuntimeError('capability principal boundary not found')
resolver = r'''async fn resolve_capability_principal(
    state: &AppState,
    arguments: &Map<String, Value>,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
    transport_principal: Option<&execution::Principal>,
) -> Result<execution::Principal, &'static str> {
    if let Some(principal) = transport_principal {
        return Ok(principal.clone());
    }

    resolve_capability_identity(state, arguments, participant_id, capability, resource).await?;
    Ok(execution::Principal {
        provider: "participant-hmac".to_owned(),
        subject: participant_id.to_owned(),
    })
}
'''
mcp = mcp[:start] + resolver + mcp[end:]

mcp = replace_fn(mcp, 'blackboard_execution_receipt', 'blackboard_execution_audit', r'''async fn blackboard_execution_receipt(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "intent_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let intent_id = match arguments.get("intent_id").and_then(Value::as_str) {
        Some(value) => match execution::normalize_intent_id(value) {
            Ok(value) => value,
            Err(()) => return tool_error("invalid_intent_id"),
        },
        None => return tool_error("invalid_intent_id"),
    };
    let bearer_caller = transport_principal.is_some();
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_RECEIPT,
        Some(&intent_id),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let (schema_current, allowed, receipt) = match with_db_read_only(state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)? {
            return Ok((false, false, None));
        }
        let decision = authorization::evaluate_read_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_RECEIPT,
            Some(&lookup_intent),
        )?;
        let receipt = if decision.allowed {
            execution::get_execution_receipt(conn, &lookup_participant, &lookup_intent)?
        } else {
            None
        };
        Ok((true, decision.allowed, receipt))
    })
    .await
    {
        Ok(value) => value,
        Err(()) => return tool_error("database_unavailable"),
    };
    if !schema_current {
        return tool_error("authorization_schema_not_current");
    }
    if !allowed {
        // Preserve the historical adapter behavior: Bearer callers were denied
        // during principal resolution, while participant-HMAC reads obscured a
        // denied receipt as not found.
        return tool_error(if bearer_caller {
            "forbidden"
        } else {
            "execution_not_found"
        });
    }
    let Some(receipt) = receipt else {
        return tool_error("execution_not_found");
    };
    tool_success(json!({"execution": receipt}))
}''')

mcp = replace_fn(mcp, 'blackboard_execution_audit', 'blackboard_execution_audit_integrity', r'''async fn blackboard_execution_audit(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "intent_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let intent_id = match arguments.get("intent_id").and_then(Value::as_str) {
        Some(value) => match execution::normalize_intent_id(value) {
            Ok(value) => value,
            Err(()) => return tool_error("invalid_intent_id"),
        },
        None => return tool_error("invalid_intent_id"),
    };
    let bearer_caller = transport_principal.is_some();
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_AUDIT,
        Some(&intent_id),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let (schema_current, allowed, audit) = match with_db_read_only(state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)? {
            return Ok((false, false, None));
        }
        let decision = authorization::evaluate_read_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )?;
        let audit = if decision.allowed {
            execution::get_execution_audit_bundle(conn, &lookup_participant, &lookup_intent)?
        } else {
            None
        };
        Ok((true, decision.allowed, audit))
    })
    .await
    {
        Ok(value) => value,
        Err(()) => return tool_error("database_unavailable"),
    };
    if !schema_current {
        return tool_error("authorization_schema_not_current");
    }
    if !allowed {
        return tool_error(if bearer_caller {
            "forbidden"
        } else {
            "execution_not_found"
        });
    }
    let Some(audit) = audit else {
        return tool_error("execution_not_found");
    };
    tool_success(json!({"audit": audit}))
}''')

mcp = replace_fn(mcp, 'blackboard_execution_audit_integrity', 'blackboard_execution_audit_sweep', r'''async fn blackboard_execution_audit_integrity(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "intent_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let intent_id = match arguments.get("intent_id").and_then(Value::as_str) {
        Some(value) => match execution::normalize_intent_id(value) {
            Ok(value) => value,
            Err(()) => return tool_error("invalid_intent_id"),
        },
        None => return tool_error("invalid_intent_id"),
    };
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_AUDIT,
        Some(&intent_id),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let (schema_current, allowed, report) = match with_db_read_only(state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)? {
            return Ok((false, false, None));
        }
        let decision = authorization::evaluate_read_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT,
            Some(&lookup_intent),
        )?;
        let report = if decision.allowed {
            Some(execution::verify_execution_audit_integrity(
                conn,
                &lookup_participant,
                &lookup_intent,
            )?)
        } else {
            None
        };
        Ok((true, decision.allowed, report))
    })
    .await
    {
        Ok(value) => value,
        Err(()) => return tool_error("database_unavailable"),
    };
    if !schema_current {
        return tool_error("authorization_schema_not_current");
    }
    if !allowed {
        return tool_error("forbidden");
    }
    let report = report.expect("authorized integrity verification must produce a report");
    tool_success(json!({"integrity": report}))
}''')

mcp = replace_fn(mcp, 'blackboard_execution_audit_sweep', 'blackboard_authorization_decision', r'''async fn blackboard_execution_audit_sweep(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_AUDIT_SWEEP,
        Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (schema_current, allowed, report) = match with_db_read_only(state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)? {
            return Ok((false, false, None));
        }
        let decision = authorization::evaluate_read_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_AUDIT_SWEEP,
            Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
        )?;
        let report = if decision.allowed {
            Some(execution::sweep_execution_audit_integrity(conn)?)
        } else {
            None
        };
        Ok((true, decision.allowed, report))
    })
    .await
    {
        Ok(value) => value,
        Err(()) => return tool_error("database_unavailable"),
    };
    if !schema_current {
        return tool_error("authorization_schema_not_current");
    }
    if !allowed {
        return tool_error("forbidden");
    }
    let report = report.expect("authorized audit sweep must produce a report");
    tool_success(json!({"sweep": report}))
}''')

mcp = replace_fn(mcp, 'blackboard_authorization_policy_integrity', 'blackboard_read', r'''async fn blackboard_authorization_policy_integrity(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(arguments, &["participant_id", "auth"]) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let principal = match resolve_capability_principal(
        state,
        arguments,
        &participant_id,
        authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
        Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        transport_principal,
    )
    .await
    {
        Ok(principal) => principal,
        Err(code) => return tool_error(code),
    };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (schema_current, allowed, report) = match with_db_read_only(state, move |conn| {
        if !authorization::effective_grants_schema_current(conn)?
            || !authorization::authorization_integrity_schema_current(conn)?
        {
            return Ok((false, false, None));
        }
        let decision = authorization::evaluate_read_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        )?;
        if !decision.allowed {
            return Ok((true, false, None));
        }
        Ok((
            true,
            true,
            Some(authorization::audit_authorization_integrity(conn)?),
        ))
    })
    .await
    {
        Ok(value) => value,
        Err(()) => return tool_error("database_unavailable"),
    };
    if !schema_current {
        return tool_error("authorization_schema_not_current");
    }
    if !allowed {
        return tool_error("forbidden");
    }
    let report = report.expect("authorized current-schema policy integrity read must produce report");
    tool_success(json!({"integrity": report}))
}''')

mcp_path.write_text(mcp)

# Add behavior and source regressions.
test_path = Path('src/mcp_contract_tests.rs')
tests = test_path.read_text()

stale_test = 'privileged_mcp_reads_refuse_stale_authorization_schema_without_repair'
if stale_test not in tests:
    tests += r'''

#[tokio::test]
async fn privileged_mcp_reads_refuse_stale_authorization_schema_without_repair() {
    let fixture = fixture();
    let conn = db::connect(&fixture.db_path).unwrap();
    authorization::ensure_grant_schema(&conn).unwrap();
    conn.execute("DROP TABLE principal_grants", []).unwrap();
    let schema_version_before: i64 = conn
        .query_row("PRAGMA schema_version", [], |row| row.get(0))
        .unwrap();
    drop(conn);

    let main_before = std::fs::read(&fixture.db_path).unwrap();
    let sidecars_before = ["-wal", "-shm", "-journal"]
        .into_iter()
        .map(|suffix| {
            let path = PathBuf::from(format!("{}{}", fixture.db_path.display(), suffix));
            let bytes = path.exists().then(|| std::fs::read(&path).unwrap());
            (path, bytes)
        })
        .collect::<Vec<_>>();

    let cases = [
        (
            "blackboard_execution_receipt",
            authorization::READ_EXECUTION_RECEIPT,
            Some("issue115-stale-receipt"),
        ),
        (
            "blackboard_execution_audit",
            authorization::READ_EXECUTION_AUDIT,
            Some("issue115-stale-audit"),
        ),
        (
            "blackboard_execution_audit_integrity",
            authorization::READ_EXECUTION_AUDIT,
            Some("issue115-stale-integrity"),
        ),
        (
            "blackboard_execution_audit_sweep",
            authorization::READ_EXECUTION_AUDIT_SWEEP,
            Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE),
        ),
        (
            "blackboard_authorization_policy_integrity",
            authorization::READ_AUTHORIZATION_POLICY_INTEGRITY,
            Some(authorization::AUTHORIZATION_POLICY_INTEGRITY_RESOURCE),
        ),
    ];

    for (offset, (tool, capability, resource)) in cases.into_iter().enumerate() {
        let mut arguments = capability_arguments(
            &fixture.single_secret,
            "single-main",
            capability,
            resource,
        );
        if tool == "blackboard_execution_receipt"
            || tool == "blackboard_execution_audit"
            || tool == "blackboard_execution_audit_integrity"
        {
            arguments["intent_id"] = json!(resource.unwrap());
        }
        let value = call_tool(&fixture.router, 11500 + offset as i64, tool, arguments).await;
        assert_eq!(value["result"]["isError"], true, "{tool}");
        assert_eq!(
            tool_error_code(&value),
            "authorization_schema_not_current",
            "{tool}"
        );
    }

    let conn = db::connect_read_only(&fixture.db_path).unwrap();
    let grant_table_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'principal_grants'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(grant_table_count, 0, "MCP read repaired grant schema");
    let schema_version_after: i64 = conn
        .query_row("PRAGMA schema_version", [], |row| row.get(0))
        .unwrap();
    drop(conn);
    assert_eq!(schema_version_after, schema_version_before);
    assert_eq!(std::fs::read(&fixture.db_path).unwrap(), main_before);
    for (path, before) in sidecars_before {
        let after = path.exists().then(|| std::fs::read(&path).unwrap());
        assert_eq!(after, before, "MCP read mutated SQLite sidecar {}", path.display());
    }
}

#[test]
fn privileged_mcp_read_handlers_are_schema_pure() {
    let source = include_str!("mcp.rs");
    let functions = [
        ("blackboard_execution_receipt", "blackboard_execution_audit"),
        ("blackboard_execution_audit", "blackboard_execution_audit_integrity"),
        (
            "blackboard_execution_audit_integrity",
            "blackboard_execution_audit_sweep",
        ),
        (
            "blackboard_execution_audit_sweep",
            "blackboard_authorization_decision",
        ),
        ("blackboard_authorization_policy_integrity", "blackboard_read"),
    ];
    for (name, next) in functions {
        let start = source
            .find(&format!("async fn {name}("))
            .unwrap_or_else(|| panic!("missing {name}"));
        let end = source[start..]
            .find(&format!("\nasync fn {next}("))
            .map(|offset| start + offset)
            .unwrap_or_else(|| panic!("missing end marker for {name}"));
        let body = &source[start..end];
        assert!(body.contains("with_db_read_only("), "{name}");
        assert!(body.contains("effective_grants_schema_current("), "{name}");
        assert!(body.contains("evaluate_read_authorization("), "{name}");
        assert!(!body.contains("authorization::authorize("), "{name}");
        assert!(!body.contains("ensure_grant_schema("), "{name}");
    }

    let resolver_start = source.find("async fn resolve_capability_principal(").unwrap();
    let resolver_end = source[resolver_start..]
        .find("\nasync fn blackboard_access_context(")
        .map(|offset| resolver_start + offset)
        .unwrap();
    let resolver = &source[resolver_start..resolver_end];
    assert!(!resolver.contains("authorization::authorize("));
    assert!(!resolver.contains("ensure_grant_schema("));
}
'''

test_path.write_text(tests)
