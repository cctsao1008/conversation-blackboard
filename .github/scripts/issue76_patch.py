from pathlib import Path
import json


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:140]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


Path("src/contract_schema.rs").write_text(r'''#![cfg_attr(not(test), allow(dead_code))]

use serde_json::{json, Value};

pub const PARTICIPANT_ID_MAX: u64 = 64;
pub const INTENT_ID_MAX: u64 = 256;
pub const DELIVERY_ID_MAX: u64 = 256;

pub fn participant_id_schema() -> Value {
    json!({
        "type": "string",
        "minLength": 1,
        "maxLength": PARTICIPANT_ID_MAX,
        "description": "Durable Blackboard attribution identity. It is not a transport credential."
    })
}

pub fn conversation_ref_schema() -> Value {
    json!({
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": "Optional provider-side conversation provenance. It carries no authentication or authorization authority."
    })
}

pub fn intent_id_schema() -> Value {
    json!({
        "type": "string",
        "minLength": 1,
        "maxLength": INTENT_ID_MAX,
        "description": "Transport-independent semantic operation identity used for idempotency and receipt lookup."
    })
}

pub fn delivery_id_schema() -> Value {
    json!({
        "type": "string",
        "minLength": 1,
        "maxLength": DELIVERY_ID_MAX,
        "description": "Transport delivery provenance identity. Distinct deliveries may carry the same semantic intent_id. delivery_id is not part of the semantic execution receipt."
    })
}

pub fn principal_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "provider": {"type": "string"},
            "subject": {"type": "string"}
        },
        "required": ["provider", "subject"],
        "additionalProperties": false
    })
}

pub fn message_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "id": {"type": "integer", "minimum": 1},
            "created_at": {"type": "integer"},
            "channel": {"type": "string"},
            "source": {"type": "string"},
            "instance": {"type": "string"},
            "conversation_ref": conversation_ref_schema(),
            "kind": {"type": "string"},
            "body": {"type": "string"},
            "reply_to": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
        },
        "required": ["id", "created_at", "channel", "source", "instance", "conversation_ref", "kind", "body", "reply_to"],
        "additionalProperties": false
    })
}

pub fn access_context_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "participant_id": participant_id_schema(),
            "principal": principal_schema(),
            "capabilities": {"type": "array", "items": {"type": "string"}},
            "grants": {"type": "array"},
            "adapter": {"type": "string"}
        },
        "required": ["participant_id", "principal", "capabilities", "grants", "adapter"],
        "additionalProperties": false
    })
}

pub fn execution_receipt_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "participant_id": participant_id_schema(),
            "intent_id": intent_id_schema(),
            "intent_hash": {"type": "string"},
            "capability": {"type": "string"},
            "message_id": {"type": "integer", "minimum": 1},
            "status": {"type": "string"}
        },
        "required": ["participant_id", "intent_id", "intent_hash", "capability", "message_id", "status"],
        "additionalProperties": false,
        "description": "Durable semantic execution receipt. Transport delivery_id is intentionally excluded and remains ingress provenance."
    })
}

pub fn execution_receipt_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"execution": execution_receipt_schema()},
        "required": ["execution"],
        "additionalProperties": false
    })
}
''', encoding="utf-8")

replace_once(
    "src/main.rs",
    "mod authorization;\n",
    "mod authorization;\nmod contract_schema;\n",
)
replace_once(
    "src/main.rs",
    "#[cfg(test)]\nmod access_control_contract_tests;\n",
    "#[cfg(test)]\nmod access_control_contract_tests;\n#[cfg(test)]\nmod contract_parity_tests;\n",
)

replace_once(
    "src/mcp.rs",
    "    adapter_profile, authorization, db, execution, http::AppState, identity, model::Identity,\n    participant_auth,\n",
    "    adapter_profile, authorization, contract_schema, db, execution, http::AppState, identity,\n    model::Identity, participant_auth,\n",
)
replace_once(
    "src/mcp.rs",
    '"participant_id": {"type": "string", "minLength": 1, "maxLength": 64},',
    '"participant_id": contract_schema::participant_id_schema(),',
)
# Four occurrences are expected; replace the remaining three individually.
for _ in range(3):
    text = Path("src/mcp.rs").read_text(encoding="utf-8")
    old = '"participant_id": {"type": "string", "minLength": 1, "maxLength": 64},'
    if old not in text:
        break
    Path("src/mcp.rs").write_text(text.replace(old, '"participant_id": contract_schema::participant_id_schema(),', 1), encoding="utf-8")
replace_once(
    "src/mcp.rs",
    '"intent_id": {"type": "string", "minLength": 1, "maxLength": 256},',
    '"intent_id": contract_schema::intent_id_schema(),',
)
replace_once(
    "src/mcp.rs",
    '"messages": {"type": "array", "items": message_schema()}',
    '"messages": {"type": "array", "items": contract_schema::message_schema()}',
)
replace_once(
    "src/mcp.rs",
    '"outputSchema": access_context_output_schema(),',
    '"outputSchema": contract_schema::access_context_schema(),',
)
replace_once(
    "src/mcp.rs",
    '"outputSchema": execution_receipt_output_schema(),',
    '"outputSchema": contract_schema::execution_receipt_envelope_schema(),',
)

mcp = Path("src/mcp.rs").read_text(encoding="utf-8")
start = mcp.index("fn access_context_output_schema() -> Value {")
end = mcp.index("async fn tool_call_result(", start)
mcp = mcp[:start] + mcp[end:]
Path("src/mcp.rs").write_text(mcp, encoding="utf-8")

cargo = Path("Cargo.toml").read_text(encoding="utf-8")
if 'serde_yaml = "0.9"' not in cargo:
    cargo = cargo.replace('[dev-dependencies]\n', '[dev-dependencies]\nserde_yaml = "0.9"\n', 1)
Path("Cargo.toml").write_text(cargo, encoding="utf-8")

Path("src/contract_parity_tests.rs").write_text(r'''use std::collections::BTreeSet;

use serde_json::Value;

use crate::contract_schema;

fn names(value: &Value, pointer: &str) -> BTreeSet<String> {
    value
        .pointer(pointer)
        .and_then(Value::as_object)
        .unwrap_or_else(|| panic!("missing object at {pointer}"))
        .keys()
        .cloned()
        .collect()
}

fn yaml_json() -> Value {
    let yaml: serde_yaml::Value = serde_yaml::from_str(include_str!("../integrations/openapi.yaml"))
        .expect("openapi.yaml must parse");
    serde_json::to_value(yaml).expect("OpenAPI YAML must convert to JSON value")
}

fn utcp_json() -> Value {
    serde_json::from_str(include_str!("../integrations/utcp.json"))
        .expect("utcp.json must parse")
}

#[test]
fn canonical_semantic_identifiers_are_explicit() {
    assert_eq!(contract_schema::PARTICIPANT_ID_MAX, 64);
    assert_eq!(contract_schema::INTENT_ID_MAX, 256);
    assert_eq!(contract_schema::DELIVERY_ID_MAX, 256);
    assert!(contract_schema::conversation_ref_schema()["description"]
        .as_str()
        .unwrap()
        .contains("provenance"));
    assert!(contract_schema::delivery_id_schema()["description"]
        .as_str()
        .unwrap()
        .contains("same semantic intent_id"));
}

#[test]
fn openapi_message_and_receipt_match_canonical_rust_shapes() {
    let api = yaml_json();
    assert_eq!(
        names(&api, "/components/schemas/Message/properties"),
        names(&contract_schema::message_schema(), "/properties")
    );
    assert_eq!(
        names(&api, "/components/schemas/ExecutionReceipt/properties"),
        names(&contract_schema::execution_receipt_schema(), "/properties")
    );
    let description = api["info"]["description"].as_str().unwrap();
    assert!(description.contains("HTTP projection"));
    assert!(description.contains("not domain authority"));
}

#[test]
fn utcp_is_discovery_projection_and_tracks_http_message_shape() {
    let utcp = utcp_json();
    assert_eq!(utcp["contract_projection"]["kind"], "utcp-discovery");
    assert_eq!(utcp["contract_projection"]["authority"], "rust-application-kernel");
    let tools = utcp["tools"].as_array().unwrap();
    let read = tools.iter().find(|tool| tool["name"] == "read_messages").unwrap();
    assert_eq!(
        names(read, "/outputs/properties/messages/items/properties"),
        names(&contract_schema::message_schema(), "/properties")
    );
    assert!(tools.iter().any(|tool| tool["name"] == "access_context"));
    assert!(tools.iter().any(|tool| tool["name"] == "execution_receipt"));
}

#[test]
fn delivery_identity_never_becomes_semantic_receipt_identity() {
    let receipt = contract_schema::execution_receipt_schema();
    assert!(receipt["properties"].get("intent_id").is_some());
    assert!(receipt["properties"].get("delivery_id").is_none());
    let api = yaml_json();
    assert!(api["components"]["schemas"]["DeliveryId"]["description"]
        .as_str()
        .unwrap()
        .contains("transport"));
}
''', encoding="utf-8")

openapi = Path("integrations/openapi.yaml").read_text(encoding="utf-8")
openapi = openapi.replace(
    "    Native HTTP contract for reading, writing, and administering a conversation-blackboard.\n",
    "    Native HTTP projection for reading, writing, and administering a conversation-blackboard.\n    This OpenAPI document is an HTTP projection of Rust application semantics; it is not domain authority.\n",
    1,
)
insert_paths = r'''  /api/access-context:
    get:
      operationId: blackboardAccessContext
      summary: Read the authenticated caller's effective Blackboard access context.
      description: HTTP projection of the shared authorization kernel.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      responses:
        '200':
          description: Effective identity, principal, participant attribution, capabilities, and grants.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AccessContext'
        '401':
          $ref: '#/components/responses/Unauthorized'

  /api/executions/{intent_id}:
    get:
      operationId: blackboardExecutionReceipt
      summary: Read one durable semantic execution receipt.
      description: The intent_id is semantic identity. delivery_id remains transport ingress provenance and is intentionally absent from the receipt.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      parameters:
        - name: intent_id
          in: path
          required: true
          schema:
            $ref: '#/components/schemas/IntentId'
      responses:
        '200':
          description: Durable execution receipt.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ExecutionReceiptEnvelope'
        '400':
          description: Invalid semantic intent identifier.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '404':
          description: No durable receipt exists for this participant and intent.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

'''
openapi = openapi.replace("  /api/admin/channels:\n", insert_paths + "  /api/admin/channels:\n", 1)
openapi = openapi.replace(
    "  schemas:\n    Identity:\n",
    r'''  schemas:
    ParticipantId:
      type: string
      minLength: 1
      maxLength: 64
      description: Durable Blackboard attribution identity. It is not a transport credential.

    ConversationRef:
      type: [string, 'null']
      description: Optional provider-side conversation provenance. It carries no authentication or authorization authority.

    IntentId:
      type: string
      minLength: 1
      maxLength: 256
      description: Transport-independent semantic operation identity used for idempotency and receipt lookup.

    DeliveryId:
      type: string
      minLength: 1
      maxLength: 256
      description: Transport delivery provenance identity. Distinct deliveries may carry the same semantic intent_id; delivery_id is not part of the semantic execution receipt.

    Principal:
      type: object
      additionalProperties: false
      required: [provider, subject]
      properties:
        provider:
          type: string
        subject:
          type: string

    AccessContext:
      type: object
      additionalProperties: false
      required: [participant_id, principal, capabilities, grants]
      properties:
        identity:
          $ref: '#/components/schemas/Identity'
        principal:
          $ref: '#/components/schemas/Principal'
        participant_id:
          anyOf:
            - $ref: '#/components/schemas/ParticipantId'
            - type: 'null'
        role:
          type: string
        capabilities:
          type: array
          items:
            type: string
        grants:
          type: array

    ExecutionReceipt:
      type: object
      additionalProperties: false
      required: [participant_id, intent_id, intent_hash, capability, message_id, status]
      description: Durable semantic execution receipt. Transport delivery_id is intentionally excluded and remains ingress provenance.
      properties:
        participant_id:
          $ref: '#/components/schemas/ParticipantId'
        intent_id:
          $ref: '#/components/schemas/IntentId'
        intent_hash:
          type: string
        capability:
          type: string
        message_id:
          type: integer
          minimum: 1
        status:
          type: string

    ExecutionReceiptEnvelope:
      type: object
      additionalProperties: false
      required: [execution]
      properties:
        execution:
          $ref: '#/components/schemas/ExecutionReceipt'

    Identity:
''',
    1,
)
openapi = openapi.replace(
    "        conversation_ref:\n          type: [string, 'null']\n          description: Optional provider-side conversation provenance. It is not an authentication or authorization credential.\n",
    "        conversation_ref:\n          $ref: '#/components/schemas/ConversationRef'\n",
    1,
)
Path("integrations/openapi.yaml").write_text(openapi, encoding="utf-8")

utcp_path = Path("integrations/utcp.json")
utcp = json.loads(utcp_path.read_text(encoding="utf-8"))
utcp["contract_projection"] = {
    "kind": "utcp-discovery",
    "authority": "rust-application-kernel",
    "http_projection": "integrations/openapi.yaml",
    "note": "UTCP describes discovery/invocation metadata and is not domain authority. intent_id is semantic identity; delivery_id is transport provenance."
}

existing = {tool["name"] for tool in utcp["tools"]}
if "access_context" not in existing:
    utcp["tools"].append({
        "name": "access_context",
        "description": "Read effective capabilities and grants from the shared Blackboard authorization kernel. UTCP is discovery metadata, not the authorization source of truth.",
        "inputs": {"type": "object", "additionalProperties": False, "properties": {}},
        "outputs": {
            "type": "object",
            "required": ["participant_id", "principal", "capabilities", "grants"],
            "properties": {
                "participant_id": {"type": ["string", "null"]},
                "principal": {"type": "object", "required": ["provider", "subject"], "properties": {"provider": {"type": "string"}, "subject": {"type": "string"}}},
                "capabilities": {"type": "array", "items": {"type": "string"}},
                "grants": {"type": "array"}
            }
        },
        "tags": ["blackboard", "authorization", "read"],
        "tool_call_template": {
            "name": "blackboard_access_context_http",
            "call_template_type": "http",
            "url": "${BLACKBOARD_URL}/api/access-context",
            "http_method": "GET",
            "content_type": "application/json",
            "body_field": None,
            "auth": {"auth_type": "api_key", "api_key": "Bearer ${BLACKBOARD_TOKEN}", "var_name": "Authorization", "location": "header"}
        }
    })
if "execution_receipt" not in existing:
    utcp["tools"].append({
        "name": "execution_receipt",
        "description": "Read a durable semantic execution receipt by intent_id. delivery_id is transport provenance and is intentionally not part of the receipt.",
        "inputs": {
            "type": "object",
            "additionalProperties": False,
            "required": ["intent_id"],
            "properties": {"intent_id": {"type": "string", "minLength": 1, "maxLength": 256}}
        },
        "outputs": {
            "type": "object",
            "required": ["execution"],
            "properties": {
                "execution": {
                    "type": "object",
                    "required": ["participant_id", "intent_id", "intent_hash", "capability", "message_id", "status"],
                    "properties": {
                        "participant_id": {"type": "string"},
                        "intent_id": {"type": "string"},
                        "intent_hash": {"type": "string"},
                        "capability": {"type": "string"},
                        "message_id": {"type": "integer", "minimum": 1},
                        "status": {"type": "string"}
                    }
                }
            }
        },
        "tags": ["blackboard", "execution", "read"],
        "tool_call_template": {
            "name": "blackboard_execution_receipt_http",
            "call_template_type": "http",
            "url": "${BLACKBOARD_URL}/api/executions/${intent_id}",
            "http_method": "GET",
            "content_type": "application/json",
            "body_field": None,
            "auth": {"auth_type": "api_key", "api_key": "Bearer ${BLACKBOARD_TOKEN}", "var_name": "Authorization", "location": "header"}
        }
    })
utcp_path.write_text(json.dumps(utcp, indent=2) + "\n", encoding="utf-8")

Path("docs/contract-projections.md").write_text(r'''# Contract Projections

Conversation Blackboard has one application/domain semantic kernel and multiple external projections.

## Authority order

1. Rust application semantics and persistence/execution/authorization invariants are authoritative.
2. MCP schemas are adapter projections over those semantics.
3. OpenAPI is the native HTTP projection.
4. UTCP is discovery/invocation metadata over the HTTP/native capability surface.

No external description becomes a second source of domain truth.

## Canonical shared shapes

`src/contract_schema.rs` owns reusable externally visible semantic shapes used directly by MCP and by parity tests for the HTTP/UTCP projections. It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, and execution receipt.

`intent_id` identifies a semantic operation across transports. `delivery_id` identifies one transport delivery and belongs to ingress provenance. Multiple delivery IDs may carry the same intent ID, so `delivery_id` is deliberately absent from the semantic execution receipt.

## Generation and validation rule

> Generate first. Validate second. Never maintain identical semantics manually in multiple contracts.

Where runtime adapter schemas can directly reuse Rust schema builders, they do. Where an external file format must remain a checked-in projection, `contract_parity_tests` structurally validates the duplicated shape in normal Rust CI. Contract drift therefore fails the same Linux/Windows validation path as implementation drift.
''', encoding="utf-8")
