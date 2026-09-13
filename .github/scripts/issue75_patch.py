from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


Path("src/adapter_profile.rs").write_text(
    '''#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AdapterProfile {
    pub name: &'static str,
    pub transport: &'static str,
    pub capabilities: &'static [&'static str],
    pub asynchronous: bool,
}

pub const REST: AdapterProfile = AdapterProfile {
    name: "rest",
    transport: "http",
    capabilities: &["read_messages", "post_message", "access_context", "execution_receipt", "manage_channels"],
    asynchronous: false,
};

pub const MCP: AdapterProfile = AdapterProfile {
    name: "mcp",
    transport: "streamable-http",
    capabilities: &["read_messages", "post_message", "access_context", "execution_receipt"],
    asynchronous: false,
};

pub const GITHUB_MAILBOX: AdapterProfile = AdapterProfile {
    name: "github-mailbox",
    transport: "github-issues-webhook",
    capabilities: &["post_message"],
    asynchronous: true,
};

pub const CLI: AdapterProfile = AdapterProfile {
    name: "cli",
    transport: "native-process",
    capabilities: &["participant_admin", "database_admin", "client_projection"],
    asynchronous: false,
};

pub const PROFILES: &[AdapterProfile] = &[REST, MCP, GITHUB_MAILBOX, CLI];

pub fn profile(name: &str) -> Option<&'static AdapterProfile> {
    PROFILES.iter().find(|profile| profile.name == name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn required_adapter_profiles_are_explicit() {
        assert_eq!(profile("rest"), Some(&REST));
        assert_eq!(profile("mcp"), Some(&MCP));
        assert_eq!(profile("github-mailbox"), Some(&GITHUB_MAILBOX));
    }

    #[test]
    fn semantic_parity_does_not_require_transport_parity() {
        assert!(REST.capabilities.contains(&"post_message"));
        assert!(MCP.capabilities.contains(&"post_message"));
        assert!(GITHUB_MAILBOX.capabilities.contains(&"post_message"));
        assert!(REST.capabilities.contains(&"access_context"));
        assert!(MCP.capabilities.contains(&"access_context"));
        assert!(!GITHUB_MAILBOX.capabilities.contains(&"access_context"));
        assert!(GITHUB_MAILBOX.asynchronous);
    }
}
''',
    encoding="utf-8",
)

replace_once("src/main.rs", "mod admin;\nmod authorization;", "mod admin;\nmod adapter_profile;\nmod authorization;")

replace_once(
    "src/participant_auth.rs",
    "#[cfg(test)]\n#[allow(clippy::too_many_arguments)]\npub fn compute_write_proof(",
    '''pub fn canonical_capability_bytes(
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
) -> Vec<u8> {
    let mut payload = BTreeMap::<String, Value>::new();
    payload.insert("auth_version".into(), json!(AUTH_SCHEME));
    payload.insert("capability".into(), json!(capability));
    payload.insert("participant_id".into(), json!(participant_id));
    payload.insert("purpose".into(), json!("blackboard-capability-v1"));
    payload.insert("resource".into(), json!(resource));
    serde_json::to_vec(&payload).expect("canonical capability payload must serialize")
}

#[cfg(test)]
pub fn compute_capability_proof(
    secret: &str,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
) -> Option<String> {
    let canonical = canonical_capability_bytes(participant_id, capability, resource);
    compute_message_proof(secret, &canonical)
}

pub fn verify_capability_proof(
    secret: &str,
    proof: &str,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
) -> bool {
    let canonical = canonical_capability_bytes(participant_id, capability, resource);
    verify_message_proof(secret, proof, &canonical)
}

#[cfg(test)]
#[allow(clippy::too_many_arguments)]
pub fn compute_write_proof(''',
)

replace_once(
    "src/execution.rs",
    "}\n\npub fn execute_message_intent(",
    '''}

pub fn get_execution_receipt(
    conn: &Connection,
    participant_id: &str,
    intent_id: &str,
) -> rusqlite::Result<Option<ExecutionReceipt>> {
    ensure_execution_tables(conn)?;
    conn.query_row(
        "SELECT participant_id, intent_id, intent_hash, capability, message_id, status\n         FROM execution_receipts\n         WHERE participant_id = ?1 AND intent_id = ?2",
        params![participant_id, intent_id],
        |row| {
            Ok(ExecutionReceipt {
                participant_id: row.get(0)?,
                intent_id: row.get(1)?,
                intent_hash: row.get(2)?,
                capability: row.get(3)?,
                message_id: row.get(4)?,
                status: row.get(5)?,
            })
        },
    )
    .optional()
}

pub fn execute_message_intent(''',
)

replace_once(
    "src/access_api.rs",
    '''    let receipt = with_db(&state, move |conn| {
        execution::ensure_execution_tables(conn)?;
        conn.query_row(
            "SELECT participant_id, intent_id, intent_hash, capability, message_id, status\\n             FROM execution_receipts\\n             WHERE participant_id = ?1 AND intent_id = ?2",
            rusqlite::params![participant_id, intent_id],
            |row| {
                Ok(execution::ExecutionReceipt {
                    participant_id: row.get(0)?,
                    intent_id: row.get(1)?,
                    intent_hash: row.get(2)?,
                    capability: row.get(3)?,
                    message_id: row.get(4)?,
                    status: row.get(5)?,
                })
            },
        )
        .optional()
    })''',
    '''    let receipt = with_db(&state, move |conn| {
        execution::get_execution_receipt(conn, &participant_id, &intent_id)
    })''',
)
replace_once("src/access_api.rs", "use rusqlite::OptionalExtension;\n", "")

replace_once(
    "src/mcp.rs",
    "use crate::{db, execution, http::AppState, identity, model::Identity, participant_auth};",
    '''use crate::{
    adapter_profile, authorization, db, execution, http::AppState, identity, model::Identity,
    participant_auth,
};''',
)
replace_once(
    "src/mcp.rs",
    '        "instructions": "Read public channels with blackboard_read without auth. Private-channel reads require participant_id plus an hmac-sha256-v1 proof over the canonical read request. Participant writes require an hmac-sha256-v1 proof over the canonical write request. The server owns source/instance provenance. Exact retries with the same nonce are idempotent."',
    '        "instructions": "MCP is an adapter over Blackboard domain semantics. Use blackboard_read / blackboard_write for messages, blackboard_access_context for effective grants, and blackboard_execution_receipt for semantic execution read-back. Authenticated calls use hmac-sha256-v1 proofs bound to the canonical request."',
)

replace_once(
    "src/mcp.rs",
    '''            }
        ]
    })
}

fn read_output_schema() -> Value {''',
    '''            },
            {
                "name": "blackboard_access_context",
                "title": "Read Blackboard Access Context",
                "description": "Return the authenticated participant's effective capability grants without exposing credentials.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": {"type": "string", "minLength": 1, "maxLength": 64},
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": access_context_output_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            },
            {
                "name": "blackboard_execution_receipt",
                "title": "Read Blackboard Execution Receipt",
                "description": "Read back the durable semantic execution receipt for one authenticated participant intent.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": {"type": "string", "minLength": 1, "maxLength": 64},
                        "intent_id": {"type": "string", "minLength": 1, "maxLength": 256},
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "intent_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": execution_receipt_output_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            }
        ]
    })
}

fn read_output_schema() -> Value {''',
)

replace_once(
    "src/mcp.rs",
    "fn message_schema() -> Value {",
    '''fn access_context_output_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "participant_id": {"type": "string"},
            "principal": {
                "type": "object",
                "properties": {"provider": {"type": "string"}, "subject": {"type": "string"}},
                "required": ["provider", "subject"],
                "additionalProperties": false
            },
            "capabilities": {"type": "array", "items": {"type": "string"}},
            "grants": {"type": "array"},
            "adapter": {"type": "string"}
        },
        "required": ["participant_id", "principal", "capabilities", "grants", "adapter"],
        "additionalProperties": false
    })
}

fn execution_receipt_output_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "execution": {
                "type": "object",
                "properties": {
                    "participant_id": {"type": "string"},
                    "intent_id": {"type": "string"},
                    "intent_hash": {"type": "string"},
                    "capability": {"type": "string"},
                    "message_id": {"type": "integer", "minimum": 1},
                    "status": {"type": "string"}
                },
                "required": ["participant_id", "intent_id", "intent_hash", "capability", "message_id", "status"],
                "additionalProperties": false
            }
        },
        "required": ["execution"],
        "additionalProperties": false
    })
}

fn message_schema() -> Value {''',
)

replace_once(
    "src/mcp.rs",
    '''        "blackboard_read" => Ok(blackboard_read(state, &arguments).await),
        "blackboard_write" => Ok(blackboard_write(state, &arguments).await),''',
    '''        "blackboard_read" => Ok(blackboard_read(state, &arguments).await),
        "blackboard_write" => Ok(blackboard_write(state, &arguments).await),
        "blackboard_access_context" => Ok(blackboard_access_context(state, &arguments).await),
        "blackboard_execution_receipt" => Ok(blackboard_execution_receipt(state, &arguments).await),''',
)

replace_once(
    "src/mcp.rs",
    "async fn blackboard_read(state: &AppState, arguments: &Map<String, Value>) -> Value {",
    '''async fn resolve_capability_identity(
    state: &AppState,
    arguments: &Map<String, Value>,
    participant_id: &str,
    capability: &str,
    resource: Option<&str>,
) -> Result<Identity, &'static str> {
    let (_, proof) = parse_auth(arguments)?;
    let proof = proof.to_owned();
    let lookup = participant_id.to_owned();
    let auth = match with_db(state, move |conn| identity::get_web_participant_auth(conn, &lookup)).await {
        Ok(Some(auth)) => auth,
        Ok(None) => return Err("unauthorized"),
        Err(()) => return Err("database_unavailable"),
    };
    if auth.auth_scheme != participant_auth::AUTH_SCHEME {
        return Err("unsupported_auth_scheme");
    }
    if !participant_auth::verify_capability_proof(&auth.auth_secret, &proof, participant_id, capability, resource) {
        return Err("unauthorized");
    }
    Ok(auth.identity)
}

async fn blackboard_access_context(state: &AppState, arguments: &Map<String, Value>) -> Value {
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
    if let Err(code) = resolve_capability_identity(state, arguments, &participant_id, "access_context", None).await {
        return tool_error(code);
    }
    let principal = execution::Principal { provider: "participant-hmac".to_owned(), subject: participant_id.clone() };
    let principal_for_grants = principal.clone();
    let lookup = participant_id.clone();
    let grants = match with_db(state, move |conn| authorization::effective_grants(conn, &principal_for_grants, &lookup)).await {
        Ok(grants) => grants,
        Err(()) => return tool_error("database_unavailable"),
    };
    let mut capabilities = grants.iter().map(|grant| grant.capability.clone()).collect::<Vec<_>>();
    capabilities.sort();
    capabilities.dedup();
    tool_success(json!({
        "participant_id": participant_id,
        "principal": principal,
        "capabilities": capabilities,
        "grants": grants,
        "adapter": adapter_profile::MCP.name
    }))
}

async fn blackboard_execution_receipt(state: &AppState, arguments: &Map<String, Value>) -> Value {
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
    if let Err(code) = resolve_capability_identity(
        state,
        arguments,
        &participant_id,
        authorization::READ_EXECUTION_RECEIPT,
        Some(&intent_id),
    )
    .await
    {
        return tool_error(code);
    }
    let principal = execution::Principal { provider: "participant-hmac".to_owned(), subject: participant_id.clone() };
    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let lookup_intent = intent_id.clone();
    let receipt = match with_db(state, move |conn| {
        if !authorization::authorize(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_EXECUTION_RECEIPT,
            Some(&lookup_intent),
        )? {
            return Ok(None);
        }
        execution::get_execution_receipt(conn, &lookup_participant, &lookup_intent)
    })
    .await
    {
        Ok(Some(receipt)) => receipt,
        Ok(None) => return tool_error("execution_not_found"),
        Err(()) => return tool_error("database_unavailable"),
    };
    tool_success(json!({"execution": receipt}))
}

async fn blackboard_read(state: &AppState, arguments: &Map<String, Value>) -> Value {''',
)

replace_once(
    "src/mcp_contract_tests.rs",
    "use crate::{db, http::AppState, identity, mcp, participant_auth};",
    "use crate::{authorization, db, http::AppState, identity, mcp, participant_auth};",
)
replace_once("src/mcp_contract_tests.rs", "    assert_eq!(tools.len(), 2);", "    assert_eq!(tools.len(), 4);")
replace_once(
    "src/mcp_contract_tests.rs",
    "#[tokio::test]\nasync fn public_read_is_unsigned_private_read_requires_valid_hmac() {",
    '''fn capability_arguments(secret: &str, participant_id: &str, capability: &str, resource: Option<&str>) -> Value {
    let proof = participant_auth::compute_capability_proof(secret, participant_id, capability, resource).unwrap();
    let mut value = json!({
        "participant_id": participant_id,
        "auth": {"scheme": participant_auth::AUTH_SCHEME, "proof": proof}
    });
    if let Some(resource) = resource {
        value["intent_id"] = json!(resource);
    }
    value
}

#[tokio::test]
async fn mcp_access_context_and_execution_receipt_project_shared_domain_state() {
    let fixture = fixture();
    let access = call_tool(
        &fixture.router,
        40,
        "blackboard_access_context",
        capability_arguments(&fixture.single_secret, "single-main", "access_context", None),
    )
    .await;
    assert!(!access["result"]["isError"].as_bool().unwrap());
    let capabilities = access["result"]["structuredContent"]["capabilities"].as_array().unwrap();
    assert!(capabilities.iter().any(|value| value == authorization::POST_MESSAGE));
    assert!(capabilities.iter().any(|value| value == authorization::READ_EXECUTION_RECEIPT));

    let written = call_tool(
        &fixture.router,
        41,
        "blackboard_write",
        write_arguments(&fixture.single_secret, "single-main", "blackboard-lounge", "message", "receipt me", None, "receipt-001"),
    )
    .await;
    let message_id = written["result"]["structuredContent"]["id"].as_i64().unwrap();

    let receipt = call_tool(
        &fixture.router,
        42,
        "blackboard_execution_receipt",
        capability_arguments(
            &fixture.single_secret,
            "single-main",
            authorization::READ_EXECUTION_RECEIPT,
            Some("receipt-001"),
        ),
    )
    .await;
    assert!(!receipt["result"]["isError"].as_bool().unwrap());
    assert_eq!(receipt["result"]["structuredContent"]["execution"]["intent_id"], "receipt-001");
    assert_eq!(receipt["result"]["structuredContent"]["execution"]["message_id"], message_id);
}

#[tokio::test]
async fn public_read_is_unsigned_private_read_requires_valid_hmac() {''',
)

Path("docs/adapter-capability-profiles.md").write_text(
    '''# Adapter Capability Profiles

Conversation Blackboard keeps domain semantics below transport adapters. REST, MCP, GitHub mailbox, CLI, UTCP descriptions, and future transports are projections over the same execution and authorization kernel; none defines domain truth.

## Profiles

| Adapter | Transport | Read messages | Post message | Access context | Execution receipt | Administration | Delivery model |
| --- | --- | --- | --- | --- | --- | --- | --- |
| REST | native HTTP | yes | yes | yes | yes | human-admin routes | synchronous |
| MCP | Streamable HTTP / JSON-RPC tools | yes | yes | yes | yes | no | synchronous |
| GitHub mailbox | GitHub Issue webhook | no | yes | no | no | no | asynchronous durable courier |
| CLI | native process | client projection | client projection | indirect | indirect | participant / DB administration | local process |

These differences are intentional capability profiles, not semantic drift.

## Shared invariants

All message-writing adapters that reach the semantic execution kernel preserve the same invariants: `participant_id` remains durable attribution identity; authenticated actors normalize to a `Principal`; semantic intent is independent of delivery identity; authorization uses the common scoped policy kernel; semantic idempotency is intent-bound; and accepted execution atomically commits effect, ingress provenance, and receipt.

## REST

REST is the native HTTP projection. It exposes message read/write, `/api/access-context`, `/api/executions/{intent_id}`, and human administration where authorized. REST schemas are HTTP contracts, not canonical domain definitions.

## MCP

MCP is the first-class agent adapter. Its tool surface includes `blackboard_read`, `blackboard_write`, `blackboard_access_context`, and `blackboard_execution_receipt`. Access context projects effective grants from the shared authorization kernel. Execution receipt reads the shared durable receipt produced by semantic execution. HMAC proofs authenticate participant calls and are bound to the canonical capability request.

## GitHub mailbox

GitHub mailbox is intentionally narrower: an asynchronous durable courier for message intent. GitHub webhook authentication establishes a principal; the same execution/authorization kernel decides whether the requested participant/resource/capability is allowed. Synchronous access-context and receipt-query parity are intentionally not required.

## CLI and UTCP

The CLI is an operational/client projection rather than a competing domain API. UTCP is discovery/invocation metadata and remains downstream of application semantics.

## Boundary with #76

This document defines which capabilities each adapter intentionally exposes. Schema generation and automated parity across OpenAPI, MCP schemas, and UTCP belong to #76.
''',
    encoding="utf-8",
)
