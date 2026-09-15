from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# Reuse the canonical mechanism label from the shared OIDC module.
oidc = Path("src/oidc.rs")
s = oidc.read_text(encoding="utf-8")
s = replace_once(s, 'const OIDC_MECHANISM: &str = "oidc-bearer-jwt";', 'pub(crate) const OIDC_MECHANISM: &str = "oidc-bearer-jwt";', "OIDC mechanism visibility")
oidc.write_text(s, encoding="utf-8")

mcp = Path("src/mcp.rs")
s = mcp.read_text(encoding="utf-8")

s = replace_once(s, '    _oidc_verifier: Option<Arc<oidc::OidcVerifier>>,', '    oidc_verifier: Option<Arc<oidc::OidcVerifier>>,', "verifier state field")
s = replace_once(s, '            _oidc_verifier: oidc_verifier,', '            oidc_verifier,', "verifier state init")
s = replace_once(
    s,
    '            "content-type, accept, mcp-protocol-version, mcp-method, mcp-name, mcp-session-id",',
    '            "content-type, accept, authorization, mcp-protocol-version, mcp-method, mcp-name, mcp-session-id",',
    "CORS authorization header",
)

# Resolve a transport bearer principal once. Invalid Authorization headers never
# fall back to participant-HMAC. Absence of Authorization preserves HMAC mode.
method_anchor = '''    let method = match object.get("method").and_then(Value::as_str) {\n        Some(method) => method,\n        None if object.contains_key("result") || object.contains_key("error") => return accepted(),\n        None => return jsonrpc_http_error(StatusCode::BAD_REQUEST, id, -32600, "invalid_request"),\n    };\n\n'''
method_insert = method_anchor + '''    let transport_principal = match resolve_http_bearer_principal(&state, &headers) {\n        Ok(principal) => principal,\n        Err(()) => return bearer_unauthorized(id),\n    };\n\n'''
s = replace_once(s, method_anchor, method_insert, "HTTP bearer resolution")

s = s.replace(
    'tool_call_result(&state.app, object).await',
    'tool_call_result(&state.app, object, transport_principal.as_ref()).await',
)

# Stdio has no transport principal and remains participant-HMAC only.
s = replace_once(
    s,
    '"tools/call" => match tool_call_result(state, object).await {',
    '"tools/call" => match tool_call_result(state, object, None).await {',
    "stdio tool caller",
)

# Add transport principal to dispatcher and every tool implementation.
s = replace_once(
    s,
    '''async fn tool_call_result(\n    state: &AppState,\n    object: &Map<String, Value>,\n) -> Result<Value, &'static str> {''',
    '''async fn tool_call_result(\n    state: &AppState,\n    object: &Map<String, Value>,\n    transport_principal: Option<&execution::Principal>,\n) -> Result<Value, &'static str> {''',
    "tool_call_result signature",
)
s = replace_once(s, '"blackboard_read" => Ok(blackboard_read(state, &arguments).await),', '"blackboard_read" => Ok(blackboard_read(state, &arguments, transport_principal).await),', "read dispatch")
s = replace_once(s, '"blackboard_write" => Ok(blackboard_write(state, &arguments).await),', '"blackboard_write" => Ok(blackboard_write(state, &arguments, transport_principal).await),', "write dispatch")
s = replace_once(s, '"blackboard_access_context" => Ok(blackboard_access_context(state, &arguments).await),', '"blackboard_access_context" => Ok(blackboard_access_context(state, &arguments, transport_principal).await),', "access dispatch")
s = replace_once(s, '"blackboard_execution_receipt" => Ok(blackboard_execution_receipt(state, &arguments).await),', '"blackboard_execution_receipt" => Ok(blackboard_execution_receipt(state, &arguments, transport_principal).await),', "receipt dispatch")
s = replace_once(s, '"blackboard_execution_audit" => Ok(blackboard_execution_audit(state, &arguments).await),', '"blackboard_execution_audit" => Ok(blackboard_execution_audit(state, &arguments, transport_principal).await),', "audit dispatch")
s = replace_once(s, 'Ok(blackboard_execution_audit_integrity(state, &arguments).await)', 'Ok(blackboard_execution_audit_integrity(state, &arguments, transport_principal).await)', "integrity dispatch")
s = replace_once(s, 'Ok(blackboard_execution_audit_sweep(state, &arguments).await)', 'Ok(blackboard_execution_audit_sweep(state, &arguments, transport_principal).await)', "sweep dispatch")

# Canonical helper: bearer uses Blackboard policy; HMAC keeps the existing proof
# gate and yields the same participant-hmac Principal as before.
cap_anchor = '''async fn blackboard_access_context(state: &AppState, arguments: &Map<String, Value>) -> Value {'''
cap_helper = '''async fn resolve_capability_principal(\n    state: &AppState,\n    arguments: &Map<String, Value>,\n    participant_id: &str,\n    capability: &str,\n    resource: Option<&str>,\n    transport_principal: Option<&execution::Principal>,\n) -> Result<execution::Principal, &'static str> {\n    if let Some(principal) = transport_principal {\n        let principal = principal.clone();\n        let policy_principal = principal.clone();\n        let participant = participant_id.to_owned();\n        let capability = capability.to_owned();\n        let resource = resource.map(str::to_owned);\n        let allowed = with_db(state, move |conn| {\n            authorization::authorize(\n                conn,\n                &policy_principal,\n                &participant,\n                &capability,\n                resource.as_deref(),\n            )\n        })\n        .await\n        .map_err(|_| "database_unavailable")?;\n        if !allowed {\n            return Err("forbidden");\n        }\n        return Ok(principal);\n    }\n\n    resolve_capability_identity(state, arguments, participant_id, capability, resource).await?;\n    Ok(execution::Principal {\n        provider: "participant-hmac".to_owned(),\n        subject: participant_id.to_owned(),\n    })\n}\n\nasync fn blackboard_access_context(\n    state: &AppState,\n    arguments: &Map<String, Value>,\n    transport_principal: Option<&execution::Principal>,\n) -> Value {'''
s = replace_once(s, cap_anchor, cap_helper, "capability principal helper")

# Access context is special: there is no standalone access_context capability.
# A bearer caller may inspect its own effective authority only when Blackboard
# actually grants it at least one capability for the target participant.
old_access_auth = '''    if let Err(code) =\n        resolve_capability_identity(state, arguments, &participant_id, "access_context", None).await\n    {\n        return tool_error(code);\n    }\n    let principal = execution::Principal {\n        provider: "participant-hmac".to_owned(),\n        subject: participant_id.clone(),\n    };'''
new_access_auth = '''    let principal = if let Some(principal) = transport_principal {\n        principal.clone()\n    } else {\n        if let Err(code) =\n            resolve_capability_identity(state, arguments, &participant_id, "access_context", None).await\n        {\n            return tool_error(code);\n        }\n        execution::Principal {\n            provider: "participant-hmac".to_owned(),\n            subject: participant_id.clone(),\n        }\n    };'''
s = replace_once(s, old_access_auth, new_access_auth, "access principal")
old_grants_end = '''    let mut capabilities = grants\n        .iter()'''
new_grants_end = '''    if transport_principal.is_some() && grants.is_empty() {\n        return tool_error("forbidden");\n    }\n    let mut capabilities = grants\n        .iter()'''
s = replace_once(s, old_grants_end, new_grants_end, "access bearer no grant denial")

# Receipt/audit/integrity/sweep use the canonical principal helper, then retain
# their existing policy checks and data retrieval.
for name in ["blackboard_execution_receipt", "blackboard_execution_audit"]:
    old = f'async fn {name}(state: &AppState, arguments: &Map<String, Value>) -> Value {{'
    new = f'''async fn {name}(\n    state: &AppState,\n    arguments: &Map<String, Value>,\n    transport_principal: Option<&execution::Principal>,\n) -> Value {{'''
    s = replace_once(s, old, new, f"{name} signature")

s = replace_once(
    s,
    '''async fn blackboard_execution_audit_integrity(\n    state: &AppState,\n    arguments: &Map<String, Value>,\n) -> Value {''',
    '''async fn blackboard_execution_audit_integrity(\n    state: &AppState,\n    arguments: &Map<String, Value>,\n    transport_principal: Option<&execution::Principal>,\n) -> Value {''',
    "integrity signature",
)
s = replace_once(
    s,
    '''async fn blackboard_execution_audit_sweep(\n    state: &AppState,\n    arguments: &Map<String, Value>,\n) -> Value {''',
    '''async fn blackboard_execution_audit_sweep(\n    state: &AppState,\n    arguments: &Map<String, Value>,\n    transport_principal: Option<&execution::Principal>,\n) -> Value {''',
    "sweep signature",
)

# Replace the four repeated HMAC-only authorization blocks plus synthetic principal.
def replace_policy_block(text: str, capability: str, resource_expr: str, label: str) -> str:
    old = f'''    if let Err(code) = resolve_capability_identity(\n        state,\n        arguments,\n        &participant_id,\n        {capability},\n        {resource_expr},\n    )\n    .await\n    {{\n        return tool_error(code);\n    }}\n    let principal = execution::Principal {{\n        provider: "participant-hmac".to_owned(),\n        subject: participant_id.clone(),\n    }};'''
    new = f'''    let principal = match resolve_capability_principal(\n        state,\n        arguments,\n        &participant_id,\n        {capability},\n        {resource_expr},\n        transport_principal,\n    )\n    .await\n    {{\n        Ok(principal) => principal,\n        Err(code) => return tool_error(code),\n    }};'''
    return replace_once(text, old, new, label)

s = replace_policy_block(s, "authorization::READ_EXECUTION_RECEIPT", "Some(&intent_id)", "receipt principal")
s = replace_policy_block(s, "authorization::READ_EXECUTION_AUDIT", "Some(&intent_id)", "audit principal")
s = replace_policy_block(s, "authorization::READ_EXECUTION_AUDIT", "Some(&intent_id)", "integrity principal")
s = replace_policy_block(s, "authorization::READ_EXECUTION_AUDIT_SWEEP", "Some(authorization::EXECUTION_AUDIT_SWEEP_RESOURCE)", "sweep principal")

# Read path: bearer + participant uses canonical READ_MESSAGES authorization;
# no participant still follows the existing public-channel rule.
s = replace_once(
    s,
    'async fn blackboard_read(state: &AppState, arguments: &Map<String, Value>) -> Value {',
    '''async fn blackboard_read(\n    state: &AppState,\n    arguments: &Map<String, Value>,\n    transport_principal: Option<&execution::Principal>,\n) -> Value {''',
    "read signature",
)
old_read_gate = '''    let participant_id = arguments.get("participant_id").and_then(Value::as_str);\n    let has_auth = arguments.get("auth").is_some();\n    if participant_id.is_some() || has_auth {\n        let participant_id = match participant_id.and_then(identity::validate_participant_id) {\n            Some(value) => value,\n            None => return tool_error("invalid_participant_id"),\n        };\n        let (_, proof) = match parse_auth(arguments) {\n            Ok(value) => value,\n            Err(code) => return tool_error(code),\n        };\n        let proof = proof.to_owned();\n        let lookup = participant_id.clone();\n        let verify_channel = channel.clone();\n        let verified = match with_db(state, move |conn| {\n            let Some(auth) = identity::get_web_participant_auth(conn, &lookup)? else {\n                return Ok(false);\n            };\n            Ok(auth.auth_scheme == participant_auth::AUTH_SCHEME\n                && participant_auth::verify_read_proof(\n                    &auth.auth_secret,\n                    &proof,\n                    &lookup,\n                    &verify_channel,\n                    after,\n                    limit,\n                ))\n        })\n        .await\n        {\n            Ok(value) => value,\n            Err(()) => return tool_error("database_unavailable"),\n        };\n        if !verified {\n            return tool_error("unauthorized");\n        }\n    } else {'''
new_read_gate = '''    let participant_id = arguments.get("participant_id").and_then(Value::as_str);\n    let has_auth = arguments.get("auth").is_some();\n    if participant_id.is_some() || has_auth {\n        let participant_id = match participant_id.and_then(identity::validate_participant_id) {\n            Some(value) => value,\n            None => return tool_error("invalid_participant_id"),\n        };\n        if let Some(principal) = transport_principal {\n            let principal = principal.clone();\n            let lookup = participant_id.clone();\n            let verify_channel = channel.clone();\n            let allowed = match with_db(state, move |conn| {\n                authorization::authorize(\n                    conn,\n                    &principal,\n                    &lookup,\n                    authorization::READ_MESSAGES,\n                    Some(&verify_channel),\n                )\n            })\n            .await\n            {\n                Ok(value) => value,\n                Err(()) => return tool_error("database_unavailable"),\n            };\n            if !allowed {\n                return tool_error("forbidden");\n            }\n        } else {\n            let (_, proof) = match parse_auth(arguments) {\n                Ok(value) => value,\n                Err(code) => return tool_error(code),\n            };\n            let proof = proof.to_owned();\n            let lookup = participant_id.clone();\n            let verify_channel = channel.clone();\n            let verified = match with_db(state, move |conn| {\n                let Some(auth) = identity::get_web_participant_auth(conn, &lookup)? else {\n                    return Ok(false);\n                };\n                Ok(auth.auth_scheme == participant_auth::AUTH_SCHEME\n                    && participant_auth::verify_read_proof(\n                        &auth.auth_secret,\n                        &proof,\n                        &lookup,\n                        &verify_channel,\n                        after,\n                        limit,\n                    ))\n            })\n            .await\n            {\n                Ok(value) => value,\n                Err(()) => return tool_error("database_unavailable"),\n            };\n            if !verified {\n                return tool_error("unauthorized");\n            }\n        }\n    } else {'''
s = replace_once(s, old_read_gate, new_read_gate, "read auth branching")

# Write path: bearer skips HMAC proof but not Blackboard authorization. The
# existing atomic execution kernel receives the OIDC principal and enforces the
# grant (including intent-bound/one-shot delegated grants) in-transaction.
s = replace_once(
    s,
    'async fn blackboard_write(state: &AppState, arguments: &Map<String, Value>) -> Value {',
    '''async fn blackboard_write(\n    state: &AppState,\n    arguments: &Map<String, Value>,\n    transport_principal: Option<&execution::Principal>,\n) -> Value {''',
    "write signature",
)
s = replace_once(
    s,
    '''        &nonce,\n    )\n    .await''',
    '''        &nonce,\n        transport_principal,\n    )\n    .await''',
    "write identity caller",
)
old_write_principal = '''    let principal = execution::Principal {\n        provider: "participant-hmac".to_owned(),\n        subject: participant_id.clone(),\n    };\n    let authority = execution::AuthorityContext {\n        principal: principal.clone(),\n        mechanism: participant_auth::AUTH_SCHEME.to_owned(),\n    };'''
new_write_principal = '''    let (principal, mechanism) = match transport_principal {\n        Some(principal) => (principal.clone(), oidc::OIDC_MECHANISM.to_owned()),\n        None => (\n            execution::Principal {\n                provider: "participant-hmac".to_owned(),\n                subject: participant_id.clone(),\n            },\n            participant_auth::AUTH_SCHEME.to_owned(),\n        ),\n    };\n    let authority = execution::AuthorityContext {\n        principal: principal.clone(),\n        mechanism,\n    };'''
s = replace_once(s, old_write_principal, new_write_principal, "write authority principal")
s = replace_once(
    s,
    '''        delivery_id: format!("mcp-hmac:{participant_id}:{nonce}"),''',
    '''        delivery_id: match transport_principal {\n            Some(_) => format!("mcp-bearer:{participant_id}:{nonce}"),\n            None => format!("mcp-hmac:{participant_id}:{nonce}"),\n        },''',
    "write delivery identity",
)
s = replace_once(
    s,
    '''    nonce: &str,\n) -> Result<Identity, &'static str> {\n    let (_, proof) = parse_auth(arguments)?;''',
    '''    nonce: &str,\n    transport_principal: Option<&execution::Principal>,\n) -> Result<Identity, &'static str> {\n    if transport_principal.is_some() {\n        let lookup_participant = participant_id.to_owned();\n        return match with_db(state, move |conn| {\n            identity::get_web_participant(conn, &lookup_participant)\n        })\n        .await\n        {\n            Ok(Some(identity)) => Ok(identity),\n            Ok(None) => Err("forbidden"),\n            Err(()) => Err("database_unavailable"),\n        };\n    }\n    let (_, proof) = parse_auth(arguments)?;''',
    "write bearer identity",
)

# HTTP bearer parsing/verifier and a standards-compatible challenge. Resource
# metadata is intentionally a later tranche; this already guarantees strict
# Bearer precedence and never persists the raw credential.
origin_anchor = '''fn origin_allowed(headers: &HeaderMap) -> bool {'''
http_auth = '''fn resolve_http_bearer_principal(\n    state: &McpHttpState,\n    headers: &HeaderMap,\n) -> Result<Option<execution::Principal>, ()> {\n    let Some(value) = headers.get(header::AUTHORIZATION) else {\n        return Ok(None);\n    };\n    let value = value.to_str().map_err(|_| ())?;\n    let (scheme, token) = value.split_once(' ').ok_or(())?;\n    if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() {\n        return Err(());\n    }\n    let verifier = state.oidc_verifier.as_ref().ok_or(())?;\n    verifier.verify(token).map(Some).map_err(|_| ())\n}\n\nfn bearer_unauthorized(id: Value) -> Response {\n    let mut response = jsonrpc_http_error(\n        StatusCode::UNAUTHORIZED,\n        id,\n        -32001,\n        "unauthorized",\n    );\n    response\n        .headers_mut()\n        .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));\n    response\n}\n\n'''+origin_anchor
s = replace_once(s, origin_anchor, http_auth, "HTTP bearer helpers")

mcp.write_text(s, encoding="utf-8")
