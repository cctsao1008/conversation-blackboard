import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# OpenAPI: project the already-implemented read-only GET decision surface.
p = Path("integrations/openapi.yaml")
s = p.read_text(encoding="utf-8")
anchor = """  /api/authorization-policy:\n"""
route = r'''  /api/authorization-decision/explain:
    get:
      operationId: blackboardAuthorizationDecision
      summary: Explain one Blackboard authorization decision.
      description: Privileged read-only projection of the canonical authorization decision evaluator. The authenticated caller is authorized separately from the target Principal being explained. A denied target decision is successful explanation data with HTTP 200. Query parameters are part of the authenticated request target for participant-HMAC HTTP callers. The read never consumes delegated authority and legacy authorization schemas fail without migration.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      parameters:
        - name: principal_provider
          in: query
          required: true
          schema:
            type: string
            minLength: 1
            maxLength: 256
        - name: principal_subject
          in: query
          required: true
          schema:
            type: string
            minLength: 1
            maxLength: 256
        - name: participant_id
          in: query
          required: true
          description: Target Blackboard participant whose authorization context is evaluated; this is not the authenticated caller identity.
          schema:
            $ref: '#/components/schemas/ParticipantId'
        - name: capability
          in: query
          required: true
          schema:
            type: string
            minLength: 1
            maxLength: 128
        - name: resource
          in: query
          required: false
          schema:
            type: string
            minLength: 1
            maxLength: 256
        - name: intent_id
          in: query
          required: false
          schema:
            $ref: '#/components/schemas/IntentId'
      responses:
        '200':
          description: Canonical non-secret authorization decision explanation. Both allowed and denied target decisions are returned here after caller authorization succeeds.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationDecisionEnvelope'
        '400':
          description: Invalid target principal or authorization context.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '503':
          description: Authorization schema is not current. The read does not migrate or repair it.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

'''
if "/api/authorization-decision/explain:" in s:
    raise SystemExit("OpenAPI decision route already exists")
s = replace_once(s, anchor, route + anchor, "OpenAPI authorization policy route")

anchor = """    AuthorizationIntegrityViolation:\n"""
schema = r'''    AuthorizationDecisionExplanation:
      type: object
      additionalProperties: false
      required: [allowed, source, reason, grant_id, consume_on_commit]
      description: Canonical non-secret read-only explanation of one authorization decision.
      properties:
        allowed:
          type: boolean
        source:
          type: string
        reason:
          type: string
        grant_id:
          anyOf:
            - type: integer
              minimum: 1
            - type: 'null'
        consume_on_commit:
          type: boolean

    AuthorizationDecisionEnvelope:
      type: object
      additionalProperties: false
      required: [decision]
      properties:
        decision:
          $ref: '#/components/schemas/AuthorizationDecisionExplanation'

'''
if "    AuthorizationDecisionExplanation:" in s:
    raise SystemExit("OpenAPI decision schema already exists")
s = replace_once(s, anchor, schema + anchor, "OpenAPI authorization integrity schema")
p.write_text(s, encoding="utf-8")

# UTCP: discovery/invocation metadata over the same HTTP GET capability.
p = Path("integrations/utcp.json")
data = json.loads(p.read_text(encoding="utf-8"))
if any(tool.get("name") == "authorization_decision" for tool in data["tools"]):
    raise SystemExit("UTCP decision tool already exists")

decision_tool = {
    "name": "authorization_decision",
    "description": "Explain one target Blackboard authorization decision through the privileged canonical read-only evaluator. Caller authority is separate from the target Principal; denied target decisions are successful explanation data and no delegated authority is consumed.",
    "inputs": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "principal_provider",
            "principal_subject",
            "participant_id",
            "capability",
        ],
        "properties": {
            "principal_provider": {"type": "string", "minLength": 1, "maxLength": 256},
            "principal_subject": {"type": "string", "minLength": 1, "maxLength": 256},
            "participant_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "capability": {"type": "string", "minLength": 1, "maxLength": 128},
            "resource": {"type": "string", "minLength": 1, "maxLength": 256},
            "intent_id": {"type": "string", "minLength": 1, "maxLength": 256},
        },
    },
    "outputs": {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision"],
        "properties": {
            "decision": {
                "type": "object",
                "additionalProperties": False,
                "required": ["allowed", "source", "reason", "grant_id", "consume_on_commit"],
                "properties": {
                    "allowed": {"type": "boolean"},
                    "source": {"type": "string"},
                    "reason": {"type": "string"},
                    "grant_id": {
                        "anyOf": [
                            {"type": "integer", "minimum": 1},
                            {"type": "null"},
                        ]
                    },
                    "consume_on_commit": {"type": "boolean"},
                },
            }
        },
    },
    "tags": ["blackboard", "authorization", "decision", "privileged", "read"],
    "tool_call_template": {
        "name": "blackboard_authorization_decision_http",
        "call_template_type": "http",
        "url": "${BLACKBOARD_URL}/api/authorization-decision/explain",
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
    (i for i, tool in enumerate(data["tools"]) if tool.get("name") == "authorization_policy"),
    len(data["tools"]),
)
data["tools"].insert(insert_at, decision_tool)
p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

# Rust parity: canonical decision shape must remain the source of truth.
p = Path("src/contract_parity_tests.rs")
s = p.read_text(encoding="utf-8")
anchor = '''#[test]\nfn authorization_policy_integrity_contracts_match_canonical_rust_shape() {'''
test = r'''#[test]
fn authorization_decision_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    let path = &api["paths"]["/api/authorization-decision/explain"]["get"];
    assert!(path.is_object());
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationDecisionExplanation/properties"
        ),
        names(
            &contract_schema::authorization_decision_explanation_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationDecisionEnvelope/properties"
        ),
        names(
            &contract_schema::authorization_decision_envelope_schema(),
            "/properties"
        )
    );
    let required_query = path["parameters"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|parameter| parameter["in"] == "query" && parameter["required"] == true)
        .map(|parameter| parameter["name"].as_str().unwrap().to_owned())
        .collect::<BTreeSet<_>>();
    assert_eq!(
        required_query,
        ["principal_provider", "principal_subject", "participant_id", "capability"]
            .into_iter()
            .map(str::to_owned)
            .collect()
    );

    let utcp = utcp_json();
    let decision = utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "authorization_decision")
        .expect("UTCP authorization decision projection must exist");
    assert_eq!(
        names(decision, "/outputs/properties/decision/properties"),
        names(
            &contract_schema::authorization_decision_explanation_schema(),
            "/properties"
        )
    );
    let required_inputs = decision["inputs"]["required"]
        .as_array()
        .unwrap()
        .iter()
        .map(|value| value.as_str().unwrap().to_owned())
        .collect::<BTreeSet<_>>();
    assert_eq!(required_inputs, required_query);
    assert_eq!(
        decision["tool_call_template"]["url"],
        "${BLACKBOARD_URL}/api/authorization-decision/explain"
    );
    assert_eq!(decision["tool_call_template"]["http_method"], "GET");
}

'''
if "authorization_decision_contracts_match_canonical_rust_shape" in s:
    raise SystemExit("decision parity test already exists")
s = replace_once(s, anchor, test + anchor, "authorization integrity parity test")
p.write_text(s, encoding="utf-8")
