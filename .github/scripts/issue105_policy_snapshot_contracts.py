import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# OpenAPI REST projection.
p = Path("integrations/openapi.yaml")
s = p.read_text(encoding="utf-8")
route_anchor = '''  /api/authorization-policy/integrity:\n    get:\n'''
route = '''  /api/authorization-policy:\n    get:\n      operationId: blackboardAuthorizationPolicy\n      summary: Read the explicit Blackboard authorization policy inventory.\n      description: Privileged projection of the canonical read-only authorization policy snapshot. The response includes durable and delegated grant lifecycle state without creating, repairing, normalizing, reactivating, deactivating, or consuming authority. Legacy authorization schemas fail without migration.\n      security:\n        - bearerAuth: []\n        - webSessionAuth: []\n      responses:\n        '200':\n          description: Canonical explicit authorization policy snapshot.\n          content:\n            application/json:\n              schema:\n                $ref: '#/components/schemas/AuthorizationPolicyEnvelope'\n        '401':\n          $ref: '#/components/responses/Unauthorized'\n        '403':\n          $ref: '#/components/responses/Forbidden'\n        '503':\n          description: Authorization schema is not current. The read does not migrate or repair it.\n          content:\n            application/json:\n              schema:\n                $ref: '#/components/schemas/Error'\n\n'''
s = replace_once(s, route_anchor, route + route_anchor, "OpenAPI policy snapshot route")

schema_anchor = '''    AuthorizationIntegrityViolation:\n'''
schemas = '''    DurableGrantSnapshot:\n      type: object\n      additionalProperties: false\n      required: [id, principal_provider, principal_subject, participant_id, capability, resource, status, created_at, updated_at]\n      description: Non-secret durable authorization grant inventory record.\n      properties:\n        id:\n          type: integer\n          minimum: 1\n        principal_provider:\n          type: string\n          minLength: 1\n        principal_subject:\n          type: string\n          minLength: 1\n        participant_id:\n          $ref: '#/components/schemas/ParticipantId'\n        capability:\n          type: string\n          minLength: 1\n        resource:\n          type: [string, 'null']\n        status:\n          type: string\n          enum: [active, inactive]\n        created_at:\n          type: integer\n        updated_at:\n          type: integer\n\n    DelegatedGrantSnapshot:\n      type: object\n      additionalProperties: false\n      required: [id, principal_provider, principal_subject, participant_id, capability, resource, intent_id, expires_at, one_shot, consumed_at, consumed_intent_id, status]\n      description: Non-secret delegated authorization grant inventory record including lifecycle and consumption state.\n      properties:\n        id:\n          type: integer\n          minimum: 1\n        principal_provider:\n          type: string\n          minLength: 1\n        principal_subject:\n          type: string\n          minLength: 1\n        participant_id:\n          $ref: '#/components/schemas/ParticipantId'\n        capability:\n          type: string\n          minLength: 1\n        resource:\n          type: [string, 'null']\n        intent_id:\n          anyOf:\n            - $ref: '#/components/schemas/IntentId'\n            - type: 'null'\n        expires_at:\n          type: [integer, 'null']\n        one_shot:\n          type: boolean\n        consumed_at:\n          type: [integer, 'null']\n        consumed_intent_id:\n          anyOf:\n            - $ref: '#/components/schemas/IntentId'\n            - type: 'null'\n        status:\n          type: string\n          enum: [active, inactive]\n\n    AuthorizationPolicySnapshot:\n      type: object\n      additionalProperties: false\n      required: [durable_grants, delegated_grants]\n      description: Canonical privileged read-only inventory of explicit Blackboard authorization objects.\n      properties:\n        durable_grants:\n          type: array\n          items:\n            $ref: '#/components/schemas/DurableGrantSnapshot'\n        delegated_grants:\n          type: array\n          items:\n            $ref: '#/components/schemas/DelegatedGrantSnapshot'\n\n    AuthorizationPolicyEnvelope:\n      type: object\n      additionalProperties: false\n      required: [policy]\n      properties:\n        policy:\n          $ref: '#/components/schemas/AuthorizationPolicySnapshot'\n\n'''
s = replace_once(s, schema_anchor, schemas + schema_anchor, "OpenAPI policy snapshot schemas")
p.write_text(s, encoding="utf-8")

# UTCP discovery projection. Parse and rewrite with stable two-space JSON so the
# resulting file is guaranteed syntactically valid while preserving object key order.
p = Path("integrations/utcp.json")
data = json.loads(p.read_text(encoding="utf-8"))
if any(tool.get("name") == "authorization_policy" for tool in data["tools"]):
    raise SystemExit("UTCP authorization_policy already exists")
policy_tool = {
    "name": "authorization_policy",
    "description": "Read the privileged canonical inventory of explicit durable and delegated Blackboard authorization objects. Blackboard authorization remains authoritative and the read never creates, repairs, normalizes, or consumes authority.",
    "inputs": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "outputs": {
        "type": "object",
        "additionalProperties": False,
        "required": ["policy"],
        "properties": {
            "policy": {
                "type": "object",
                "additionalProperties": False,
                "required": ["durable_grants", "delegated_grants"],
                "properties": {
                    "durable_grants": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "id", "principal_provider", "principal_subject", "participant_id",
                                "capability", "resource", "status", "created_at", "updated_at"
                            ],
                            "properties": {
                                "id": {"type": "integer", "minimum": 1},
                                "principal_provider": {"type": "string", "minLength": 1},
                                "principal_subject": {"type": "string", "minLength": 1},
                                "participant_id": {"type": "string", "minLength": 1, "maxLength": 64},
                                "capability": {"type": "string", "minLength": 1},
                                "resource": {"type": ["string", "null"]},
                                "status": {"type": "string", "enum": ["active", "inactive"]},
                                "created_at": {"type": "integer"},
                                "updated_at": {"type": "integer"},
                            },
                        },
                    },
                    "delegated_grants": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "id", "principal_provider", "principal_subject", "participant_id",
                                "capability", "resource", "intent_id", "expires_at", "one_shot",
                                "consumed_at", "consumed_intent_id", "status"
                            ],
                            "properties": {
                                "id": {"type": "integer", "minimum": 1},
                                "principal_provider": {"type": "string", "minLength": 1},
                                "principal_subject": {"type": "string", "minLength": 1},
                                "participant_id": {"type": "string", "minLength": 1, "maxLength": 64},
                                "capability": {"type": "string", "minLength": 1},
                                "resource": {"type": ["string", "null"]},
                                "intent_id": {"type": ["string", "null"], "maxLength": 256},
                                "expires_at": {"type": ["integer", "null"]},
                                "one_shot": {"type": "boolean"},
                                "consumed_at": {"type": ["integer", "null"]},
                                "consumed_intent_id": {"type": ["string", "null"], "maxLength": 256},
                                "status": {"type": "string", "enum": ["active", "inactive"]},
                            },
                        },
                    },
                },
            }
        },
    },
    "tags": ["blackboard", "authorization", "policy", "inventory", "privileged", "read"],
    "tool_call_template": {
        "name": "blackboard_authorization_policy_http",
        "call_template_type": "http",
        "url": "${BLACKBOARD_URL}/api/authorization-policy",
        "http_method": "GET",
        "content_type": "application/json",
        "body_field": None,
        "auth": {
            "auth_type": "api_key",
            "api_key": "Bearer ${BLACKBOARD_TOKEN}",
            "var_name": "Authorization",
            "location": "header",
        },
    },
}
insert_at = next(
    (i for i, tool in enumerate(data["tools"]) if tool.get("name") == "authorization_policy_integrity"),
    len(data["tools"]),
)
data["tools"].insert(insert_at, policy_tool)
p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Canonical parity regression.
p = Path("src/contract_parity_tests.rs")
s = p.read_text(encoding="utf-8")
if "authorization_policy_snapshot_contracts_match_canonical_rust_shape" in s:
    raise SystemExit("policy snapshot parity test already exists")
s += r'''

#[test]
fn authorization_policy_snapshot_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert!(api["paths"].get("/api/authorization-policy").is_some());
    assert_eq!(
        names(&api, "/components/schemas/DurableGrantSnapshot/properties"),
        names(&contract_schema::durable_grant_snapshot_schema(), "/properties")
    );
    assert_eq!(
        names(&api, "/components/schemas/DelegatedGrantSnapshot/properties"),
        names(&contract_schema::delegated_grant_snapshot_schema(), "/properties")
    );
    assert_eq!(
        names(&api, "/components/schemas/AuthorizationPolicySnapshot/properties"),
        names(&contract_schema::authorization_policy_snapshot_schema(), "/properties")
    );
    assert!(api["components"]["schemas"]["DelegatedGrantSnapshot"]["properties"]
        .get("created_at")
        .is_none());
    assert!(api["components"]["schemas"]["DelegatedGrantSnapshot"]["properties"]
        .get("updated_at")
        .is_none());

    let utcp = utcp_json();
    let policy = utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "authorization_policy")
        .expect("UTCP authorization policy snapshot projection must exist");
    assert_eq!(
        names(policy, "/outputs/properties/policy/properties"),
        names(&contract_schema::authorization_policy_snapshot_schema(), "/properties")
    );
    assert_eq!(
        names(
            policy,
            "/outputs/properties/policy/properties/durable_grants/items/properties"
        ),
        names(&contract_schema::durable_grant_snapshot_schema(), "/properties")
    );
    assert_eq!(
        names(
            policy,
            "/outputs/properties/policy/properties/delegated_grants/items/properties"
        ),
        names(&contract_schema::delegated_grant_snapshot_schema(), "/properties")
    );
    assert!(policy["outputs"]["properties"]["policy"]["properties"]["delegated_grants"]
        ["items"]["properties"]
        .get("created_at")
        .is_none());
    assert_eq!(
        policy["tool_call_template"]["url"],
        "${BLACKBOARD_URL}/api/authorization-policy"
    );
}
'''
p.write_text(s, encoding="utf-8")
