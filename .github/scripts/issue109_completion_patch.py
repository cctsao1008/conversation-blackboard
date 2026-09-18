from __future__ import annotations

import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


# 1. Keep effective-grant observation aligned with the already-existing implicit policy.
auth_path = Path("src/authorization.rs")
auth = auth_path.read_text()
auth = replace_once(
    auth,
    "            MANAGE_AUTHORIZATION_POLICY => Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),\n            _ => None,",
    "            MANAGE_AUTHORIZATION_POLICY => Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),\n            READ_AUTHORIZATION_ADMINISTRATION_HISTORY => {\n                Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE)\n            }\n            _ => None,",
    "effective history resource projection",
)

history_test = r'''
    #[test]
    fn authorization_administration_history_authority_is_narrow_and_bidirectionally_isolated() {
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
        let administrator = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "policy-admin".to_owned(),
        };
        let history_reader = Principal {
            provider: "oidc:https://issuer.example".to_owned(),
            subject: "history-reader".to_owned(),
        };

        assert!(!authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
        )
        .unwrap());
        identity::set_web_participant_role(&conn, "maker-main", "admin").unwrap();
        assert!(authorize(
            &conn,
            &human,
            "maker-main",
            READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
        )
        .unwrap());
        for principal in [&hmac, &github, &administrator, &history_reader] {
            assert!(!authorize(
                &conn,
                principal,
                "maker-main",
                READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
                Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
            )
            .unwrap());
        }

        let human_grants = effective_grants(&conn, &human, "maker-main").unwrap();
        assert!(human_grants.iter().any(|grant| {
            grant.capability == READ_AUTHORIZATION_ADMINISTRATION_HISTORY
                && grant.resource.as_deref()
                    == Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE)
                && grant.origin == "implicit"
        }));

        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                &administrator.provider,
                &administrator.subject,
                MANAGE_AUTHORIZATION_POLICY,
                AUTHORIZATION_POLICY_ADMIN_RESOURCE,
            ],
        )
        .unwrap();
        assert!(authorize(
            &conn,
            &administrator,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &administrator,
            "maker-main",
            READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
        )
        .unwrap());

        conn.execute(
            "INSERT INTO principal_grants
                (principal_provider, principal_subject, participant_id, capability, resource)
             VALUES (?1, ?2, 'maker-main', ?3, ?4)",
            params![
                &history_reader.provider,
                &history_reader.subject,
                READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
                AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE,
            ],
        )
        .unwrap();
        assert!(authorize(
            &conn,
            &history_reader,
            "maker-main",
            READ_AUTHORIZATION_ADMINISTRATION_HISTORY,
            Some(AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),
        )
        .unwrap());
        assert!(!authorize(
            &conn,
            &history_reader,
            "maker-main",
            MANAGE_AUTHORIZATION_POLICY,
            Some(AUTHORIZATION_POLICY_ADMIN_RESOURCE),
        )
        .unwrap());
    }

'''
auth = replace_once(
    auth,
    "    #[test]\n    fn authorization_decision_implicit_authority_is_human_web_admin_only() {",
    history_test + "    #[test]\n    fn authorization_decision_implicit_authority_is_human_web_admin_only() {",
    "history authority regression",
)
auth_path.write_text(auth)


# 2. Add the native HTTP contract and canonical administration-history schemas.
openapi_path = Path("integrations/openapi.yaml")
openapi = openapi_path.read_text()
route = r'''  /api/authorization-administration/history:
    get:
      operationId: blackboardAuthorizationAdministrationHistory
      summary: Read immutable authorization-administration provenance.
      description: |
        Privileged read-only projection of the canonical authorization administration history.
        The dedicated read_authorization_administration_history capability is independent from
        manage_authorization_policy. The optional participant_id query filters returned events;
        it does not narrow or widen reader authority. The endpoint uses read-only storage and
        never migrates, repairs, consumes, creates, reactivates, or deactivates authorization state.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      parameters:
        - name: participant_id
          in: query
          required: false
          description: Optional returned-history filter; not an authorization scope.
          schema:
            $ref: '#/components/schemas/ParticipantId'
      responses:
        '200':
          description: Canonical committed authorization-administration history in event-id order.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationAdministrationHistoryEnvelope'
        '400':
          description: Invalid participant history filter.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '503':
          description: Authorization administration schema is not current or storage is unavailable. The read does not repair schema.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

'''
openapi = replace_once(
    openapi,
    "  /api/authorization-grants/durable:\n",
    route + "  /api/authorization-grants/durable:\n",
    "history OpenAPI route",
)

schemas = r'''    AuthorizationAdministrationEvent:
      type: object
      additionalProperties: false
      required: [id, grant_store, grant_id, operation, actor_surface, actor_provider, actor_subject, actor_participant_id, target_principal_provider, target_principal_subject, participant_id, capability, resource, intent_id, expires_at, one_shot, before_status, after_status, created_at]
      description: Immutable non-secret authorization-administration provenance for one effective policy mutation.
      properties:
        id:
          type: integer
          minimum: 1
        grant_store:
          type: string
          enum: [durable, delegated]
        grant_id:
          type: integer
          minimum: 1
        operation:
          type: string
          enum: [create, reactivate, deactivate]
        actor_surface:
          type: string
        actor_provider:
          type: [string, 'null']
        actor_subject:
          type: [string, 'null']
        actor_participant_id:
          anyOf:
            - $ref: '#/components/schemas/ParticipantId'
            - type: 'null'
        target_principal_provider:
          type: string
        target_principal_subject:
          type: string
        participant_id:
          $ref: '#/components/schemas/ParticipantId'
        capability:
          type: string
        resource:
          type: [string, 'null']
        intent_id:
          anyOf:
            - $ref: '#/components/schemas/IntentId'
            - type: 'null'
        expires_at:
          type: [integer, 'null']
        one_shot:
          type: boolean
        before_status:
          anyOf:
            - type: string
              enum: [active, inactive]
            - type: 'null'
        after_status:
          type: string
          enum: [active, inactive]
        created_at:
          type: integer

    AuthorizationAdministrationHistory:
      type: object
      additionalProperties: false
      required: [events]
      description: Canonical read-only committed authorization-administration history.
      properties:
        events:
          type: array
          items:
            $ref: '#/components/schemas/AuthorizationAdministrationEvent'

    AuthorizationAdministrationHistoryEnvelope:
      type: object
      additionalProperties: false
      required: [history]
      properties:
        history:
          $ref: '#/components/schemas/AuthorizationAdministrationHistory'

'''
openapi = replace_once(
    openapi,
    "    DurableAuthorizationGrantCreateRequest:\n",
    schemas + "    DurableAuthorizationGrantCreateRequest:\n",
    "history OpenAPI schemas",
)
openapi_path.write_text(openapi)


# 3. Add UTCP discovery/invocation metadata for the native REST history surface.
utcp_path = Path("integrations/utcp.json")
utcp = json.loads(utcp_path.read_text())
if any(tool.get("name") == "authorization_administration_history" for tool in utcp["tools"]):
    raise RuntimeError("UTCP history tool already exists")

event_properties = {
    "id": {"type": "integer", "minimum": 1},
    "grant_store": {"type": "string", "enum": ["durable", "delegated"]},
    "grant_id": {"type": "integer", "minimum": 1},
    "operation": {"type": "string", "enum": ["create", "reactivate", "deactivate"]},
    "actor_surface": {"type": "string"},
    "actor_provider": {"type": ["string", "null"]},
    "actor_subject": {"type": ["string", "null"]},
    "actor_participant_id": {"type": ["string", "null"], "maxLength": 64},
    "target_principal_provider": {"type": "string"},
    "target_principal_subject": {"type": "string"},
    "participant_id": {"type": "string", "minLength": 1, "maxLength": 64},
    "capability": {"type": "string"},
    "resource": {"type": ["string", "null"]},
    "intent_id": {"type": ["string", "null"], "maxLength": 256},
    "expires_at": {"type": ["integer", "null"]},
    "one_shot": {"type": "boolean"},
    "before_status": {"type": ["string", "null"], "enum": ["active", "inactive", None]},
    "after_status": {"type": "string", "enum": ["active", "inactive"]},
    "created_at": {"type": "integer"},
}
event_required = list(event_properties.keys())

history_tool = {
    "name": "authorization_administration_history",
    "description": "Read immutable privileged authorization-administration provenance through the canonical read-only history surface. The optional participant_id is only a returned-history filter; history-read authority remains independent from policy mutation authority.",
    "inputs": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "participant_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "Optional returned-history filter; not an authorization scope.",
            }
        },
    },
    "outputs": {
        "type": "object",
        "additionalProperties": False,
        "required": ["history"],
        "properties": {
            "history": {
                "type": "object",
                "additionalProperties": False,
                "required": ["events"],
                "properties": {
                    "events": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": event_required,
                            "properties": event_properties,
                        },
                    }
                },
            }
        },
    },
    "tags": [
        "blackboard",
        "authorization",
        "administration",
        "history",
        "privileged",
        "read",
    ],
    "tool_call_template": {
        "name": "blackboard_authorization_administration_history_http",
        "call_template_type": "http",
        "url": "${BLACKBOARD_URL}/api/authorization-administration/history",
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
utcp["tools"].append(history_tool)
utcp_path.write_text(json.dumps(utcp, indent=2, ensure_ascii=False) + "\n")


# 4. Structural parity and adapter-source ownership guards.
parity_path = Path("src/contract_parity_tests.rs")
parity = parity_path.read_text()
new_parity = r'''

#[test]
fn authorization_administration_history_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    let operation = &api["paths"]["/api/authorization-administration/history"]["get"];
    assert!(operation.is_object());
    assert_eq!(operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/AuthorizationAdministrationHistoryEnvelope");
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationAdministrationEvent/properties"
        ),
        names(
            &contract_schema::authorization_administration_event_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationAdministrationHistory/properties"
        ),
        names(
            &contract_schema::authorization_administration_history_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationAdministrationHistoryEnvelope/properties"
        ),
        names(
            &contract_schema::authorization_administration_history_envelope_schema(),
            "/properties"
        )
    );

    let utcp = utcp_json();
    let history = utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "authorization_administration_history")
        .expect("UTCP authorization administration history projection must exist");
    assert_eq!(
        names(history, "/outputs/properties/history/properties"),
        names(
            &contract_schema::authorization_administration_history_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            history,
            "/outputs/properties/history/properties/events/items/properties"
        ),
        names(
            &contract_schema::authorization_administration_event_schema(),
            "/properties"
        )
    );
    assert_eq!(
        history["tool_call_template"]["url"],
        "${BLACKBOARD_URL}/api/authorization-administration/history"
    );
    assert_eq!(history["tool_call_template"]["http_method"], "GET");

    for (name, source) in [
        ("http", include_str!("access_api.rs")),
        ("mcp", include_str!("mcp.rs")),
    ] {
        assert!(
            source.contains("read_administration_events"),
            "{name} adapter must reuse the canonical administration history reader"
        );
        assert!(
            !source.contains("FROM authorization_admin_events"),
            "{name} adapter must not enumerate authorization administration events directly"
        );
    }
}
'''
if "authorization_administration_history_contracts_match_canonical_rust_shape" in parity:
    raise RuntimeError("history parity test already exists")
parity_path.write_text(parity.rstrip() + new_parity + "\n")


# 5. Durable architecture docs.
doc_path = Path("docs/authorization-policy.md")
doc = doc_path.read_text()
doc = replace_once(
    doc,
    "Authorization policy now exposes four deliberately separate concerns:\n\n```text\nauthorization policy snapshot\n    = which explicit durable and delegated authority objects exist\n\nauthorization policy integrity\n    = whether those stored authority objects are structurally coherent\n\nauthorization decision / explain\n    = why one requested action is allowed or denied\n\nauthorization administration\n    = creation, reactivation, deactivation, or other mutation of authority\n```",
    "Authorization policy now exposes five deliberately separate concerns:\n\n```text\nauthorization policy snapshot\n    = which explicit durable and delegated authority objects exist\n\nauthorization policy integrity\n    = whether those stored authority objects are structurally coherent\n\nauthorization decision / explain\n    = why one requested action is allowed or denied\n\nauthorization administration\n    = creation, reactivation, deactivation, or other mutation of authority\n\nauthorization administration history\n    = immutable provenance of effective committed policy mutations\n```",
    "policy observation count",
)
old_history = "`authorization_admin::read_administration_events(conn, ...)` is the canonical local provenance reader. `conversation-blackboard grant history` is its operator projection. Phase-1 REST exposes durable/delegated create and deactivation as thin authenticated projections over authorized `authorization_admin` entry points; durable create also preserves exact-scope existing/reactivation semantics. The REST adapter contains no grant lifecycle SQL and never performs schema repair. MCP grant-administration tools remain a non-goal. Source regressions prevent REST/MCP adapters from calling raw lifecycle entry points or mutating grant tables directly."
new_history = """`authorization_admin::read_administration_events(conn, ...)` is the canonical provenance reader. `conversation-blackboard grant history`, REST `GET /api/authorization-administration/history`, and MCP `blackboard_authorization_administration_history` are read-only projections over that same reader. REST/OpenAPI and UTCP expose an optional participant filter; the filter changes only returned rows and is not an authorization scope. All remote history reads use read-only database access, refuse stale administration schema without migration or repair, and contain no adapter-local event-table enumeration SQL.

History visibility has a dedicated capability and global resource:

```text
capability = read_authorization_administration_history
resource   = authorization-administration-history
```

Only Human Web admin self receives implicit history visibility. Ordinary Human Web self, participant-HMAC self, GitHub owner compatibility authority, and OIDC/Bearer identity do not. External principals require an explicit matching Blackboard grant. `manage_authorization_policy` does not imply history visibility, and history visibility does not imply mutation authority. Modern HTTP MCP may authenticate a history reader with OIDC Bearer at the transport boundary; legacy HTTP and stdio retain participant-HMAC body proof. No MCP grant-mutation tool is introduced.

Phase-1 REST mutation still exposes durable/delegated create and deactivation as thin authenticated projections over authorized `authorization_admin` entry points; durable create also preserves exact-scope existing/reactivation semantics. The REST adapter contains no grant lifecycle SQL and never performs schema repair. MCP grant administration remains a non-goal. Source regressions prevent REST/MCP adapters from calling raw lifecycle entry points or mutating grant tables directly."""
doc = replace_once(doc, old_history, new_history, "authorization history documentation")
doc_path.write_text(doc)

projection_path = Path("docs/contract-projections.md")
projection = projection_path.read_text()
projection = replace_once(
    projection,
    "It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, execution receipt, execution-audit integrity, authorization-policy integrity, authorization-policy snapshot records/envelope, and authorization-decision explanation/envelope shapes.",
    "It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, execution receipt, execution-audit integrity, authorization-policy integrity, authorization-policy snapshot records/envelope, authorization-decision explanation/envelope, and authorization-administration event/history/envelope shapes.",
    "canonical shared history shapes",
)
projection = replace_once(
    projection,
    "Authorization administration is intentionally narrower than the read-side projection set. Phase-1 mutation is REST-only and is documented in OpenAPI for durable create/reactivation/deactivation and delegated create/deactivation. Those HTTP operations are thin projections over authorized `authorization_admin` entry points; OpenAPI advertises Bearer and Human Web session authentication, while participant-HMAC JSON mutation is unsupported in Phase 1. UTCP/MCP grant-administration tools are not introduced by this phase. Parity tests therefore verify the REST operations and schemas while also guarding against accidental MCP mutation projection.",
    "Authorization administration mutation is intentionally narrower than the read-side projection set. Phase-1 mutation is REST-only and is documented in OpenAPI for durable create/reactivation/deactivation and delegated create/deactivation. Those HTTP operations are thin projections over authorized `authorization_admin` entry points; OpenAPI advertises Bearer and Human Web session authentication, while participant-HMAC JSON mutation is unsupported in Phase 1. UTCP/MCP grant-mutation tools are not introduced by this phase. Parity tests therefore verify the REST mutation operations and schemas while also guarding against accidental MCP mutation projection.\n\nAuthorization-administration history is a separate privileged read model. The canonical source is `authorization_admin::read_administration_events(conn, ...)`; REST, MCP, OpenAPI, and UTCP only project its immutable non-secret event/history contract. Visibility is guarded by `read_authorization_administration_history` on `authorization-administration-history`, independently from `manage_authorization_policy`. Optional participant filtering affects returned history only. Adapter-source parity guards prohibit direct `authorization_admin_events` enumeration SQL, and stale schema fails without migration or repair.",
    "history projection docs",
)
projection = replace_once(
    projection,
    "Authorization-policy integrity, authorization-policy snapshot, and authorization-decision explanation projection changes are complete only when canonical Rust parity tests, normal cross-platform core CI, and the UTCP discovery/invocation gate all remain green at the same final repository HEAD.",
    "Authorization-policy integrity, authorization-policy snapshot, authorization-decision explanation, and authorization-administration history projection changes are complete only when canonical Rust parity tests, normal cross-platform core CI, and the UTCP discovery/invocation gate all remain green at the same final repository HEAD.",
    "history verification boundary",
)
projection_path.write_text(projection)
