from pathlib import Path

OPENAPI = Path('integrations/openapi.yaml')
HTTP_TESTS = Path('src/http_contract_tests.rs')
PARITY = Path('src/contract_parity_tests.rs')
ADMIN = Path('src/authorization_admin.rs')
README = Path('README.md')
POLICY = Path('docs/authorization-policy.md')
PROJECTIONS = Path('docs/contract-projections.md')

openapi = OPENAPI.read_text()
marker = '  /api/authorization-decision/explain:\n'
assert marker in openapi
paths = r'''  /api/authorization-grants/durable:
    post:
      operationId: blackboardCreateDurableAuthorizationGrant
      summary: Create, reuse, or reactivate one durable authorization grant.
      description: |
        Privileged Phase-1 projection of the canonical authorization administration service.
        The dedicated manage_authorization_policy authority is required. Human Web admin self
        has narrow implicit administration authority; external Bearer callers require an explicit
        durable administration grant. Participant-HMAC mutation is unsupported in Phase 1.
        The adapter does not own lifecycle SQL or schema migration.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/DurableAuthorizationGrantCreateRequest'
      responses:
        '200':
          description: Existing active scope reused or inactive exact scope reactivated.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationGrantMutationEnvelope'
        '201':
          description: New durable grant created.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationGrantMutationEnvelope'
        '400':
          description: Invalid grant request.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '503':
          description: Authorization administration schema is not current or storage is unavailable. The request does not repair schema.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

  /api/authorization-grants/durable/{grant_id}:
    delete:
      operationId: blackboardDeactivateDurableAuthorizationGrant
      summary: Deactivate one durable authorization grant scope.
      description: |
        Privileged Phase-1 projection of canonical durable-grant deactivation. Administration
        authorization, effective lifecycle mutation, and non-secret actor provenance commit in one
        transaction. Participant-HMAC mutation is unsupported in Phase 1.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      parameters:
        - name: grant_id
          in: path
          required: true
          schema:
            type: integer
            minimum: 1
      responses:
        '200':
          description: Grant scope is inactive. Repeated deactivation is idempotent and reports rows_changed=0.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationGrantMutationEnvelope'
        '400':
          description: Invalid grant identifier.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          description: Durable grant not found.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '503':
          description: Authorization administration schema is not current or storage is unavailable. The request does not repair schema.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

  /api/authorization-grants/delegated:
    post:
      operationId: blackboardCreateDelegatedAuthorizationGrant
      summary: Create one delegated authorization grant.
      description: |
        Privileged Phase-1 projection of canonical delegated grant creation. Resource, semantic
        intent, expiry, and one-shot validation remain owned by the canonical administration
        service. Participant-HMAC mutation is unsupported in Phase 1.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/DelegatedAuthorizationGrantCreateRequest'
      responses:
        '201':
          description: New delegated grant created.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationGrantMutationEnvelope'
        '400':
          description: Invalid delegated grant request.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '503':
          description: Authorization administration schema is not current or storage is unavailable. The request does not repair schema.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

  /api/authorization-grants/delegated/{grant_id}:
    delete:
      operationId: blackboardDeactivateDelegatedAuthorizationGrant
      summary: Deactivate one delegated authorization grant.
      description: |
        Privileged Phase-1 projection of canonical delegated-grant deactivation. Delegated scope
        remains part of administration provenance, and repeated deactivation is idempotent.
        Participant-HMAC mutation is unsupported in Phase 1.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      parameters:
        - name: grant_id
          in: path
          required: true
          schema:
            type: integer
            minimum: 1
      responses:
        '200':
          description: Delegated grant is inactive. Repeated deactivation reports rows_changed=0.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationGrantMutationEnvelope'
        '400':
          description: Invalid grant identifier.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '401':
          $ref: '#/components/responses/Unauthorized'
        '403':
          $ref: '#/components/responses/Forbidden'
        '404':
          description: Delegated grant not found.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
        '503':
          description: Authorization administration schema is not current or storage is unavailable. The request does not repair schema.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'

'''
openapi = openapi.replace(marker, paths + marker, 1)

marker = '    DurableGrantSnapshot:\n'
assert marker in openapi
schemas = r'''    DurableAuthorizationGrantCreateRequest:
      type: object
      additionalProperties: false
      required: [principal_provider, principal_subject, participant_id, capability]
      properties:
        principal_provider:
          type: string
          minLength: 1
          maxLength: 256
        principal_subject:
          type: string
          minLength: 1
          maxLength: 256
        participant_id:
          $ref: '#/components/schemas/ParticipantId'
        capability:
          type: string
          minLength: 1
          maxLength: 128
        resource:
          type: [string, 'null']
          maxLength: 256

    DelegatedAuthorizationGrantCreateRequest:
      type: object
      additionalProperties: false
      required: [principal_provider, principal_subject, participant_id, capability, one_shot]
      properties:
        principal_provider:
          type: string
          minLength: 1
          maxLength: 256
        principal_subject:
          type: string
          minLength: 1
          maxLength: 256
        participant_id:
          $ref: '#/components/schemas/ParticipantId'
        capability:
          type: string
          minLength: 1
          maxLength: 128
        resource:
          type: [string, 'null']
          maxLength: 256
        intent_id:
          anyOf:
            - $ref: '#/components/schemas/IntentId'
            - type: 'null'
        expires_at:
          type: [integer, 'null']
          description: Optional future Unix timestamp enforced by the canonical administration service.
        one_shot:
          type: boolean

    AuthorizationGrantMutationOutcome:
      type: object
      additionalProperties: false
      required: [store, id, state]
      properties:
        store:
          type: string
          enum: [durable, delegated]
        id:
          type: integer
          minimum: 1
        state:
          type: string
          enum: [created, existing, reactivated, inactive]
        rows_changed:
          type: integer
          minimum: 0
          description: Present on deactivation outcomes; zero means the idempotent request changed no policy row.

    AuthorizationGrantMutationEnvelope:
      type: object
      additionalProperties: false
      required: [grant]
      properties:
        grant:
          $ref: '#/components/schemas/AuthorizationGrantMutationOutcome'

'''
openapi = openapi.replace(marker, schemas + marker, 1)
OPENAPI.write_text(openapi)

http_tests = HTTP_TESTS.read_text()
old = '''    let existing = request(&router, Method::POST, uri, Some(&session.token), Some(body)).await;'''
new = '''    let existing = request(
        &router,
        Method::POST,
        uri,
        Some(&session.token),
        Some(body.clone()),
    )
    .await;'''
assert old in http_tests
http_tests = http_tests.replace(old, new, 1)
old = '''    assert_eq!(
        after_count, event_count,
        "idempotent create emitted a false event"
    );
}'''
new = '''    assert_eq!(
        after_count, event_count,
        "idempotent create emitted a false event"
    );
    drop(conn);

    let deactivate_uri = format!("/api/authorization-grants/durable/{grant_id}");
    let deactivated = request(
        &router,
        Method::DELETE,
        &deactivate_uri,
        Some(&session.token),
        None,
    )
    .await;
    let (deactivate_status, deactivate_body) = response_json(deactivated).await;
    assert_eq!(deactivate_status, StatusCode::OK);
    assert_eq!(deactivate_body["grant"]["rows_changed"], 1);

    let reactivated = request(
        &router,
        Method::POST,
        uri,
        Some(&session.token),
        Some(body),
    )
    .await;
    let (reactivated_status, reactivated_body) = response_json(reactivated).await;
    assert_eq!(reactivated_status, StatusCode::OK);
    assert_eq!(reactivated_body["grant"]["id"], grant_id);
    assert_eq!(reactivated_body["grant"]["state"], "reactivated");
    let conn = db::connect(&fixture.db_path).unwrap();
    let operations = conn
        .prepare(
            "SELECT operation FROM authorization_admin_events
             WHERE grant_store = 'durable' AND grant_id = ?1 ORDER BY id",
        )
        .unwrap()
        .query_map([grant_id], |row| row.get::<_, String>(0))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    assert_eq!(operations, vec!["create", "deactivate", "reactivate"]);
}'''
assert old in http_tests
http_tests = http_tests.replace(old, new, 1)
HTTP_TESTS.write_text(http_tests)

parity = PARITY.read_text()
marker = '''#[test]\nfn authorization_policy_snapshot_adapters_reuse_canonical_reader_without_grant_sql() {'''
assert marker in parity
parity_test = r'''#[test]
fn authorization_administration_openapi_matches_rest_only_phase1_contract() {
    let api = yaml_json();
    let operations = [
        ("/api/authorization-grants/durable", "post"),
        ("/api/authorization-grants/durable/{grant_id}", "delete"),
        ("/api/authorization-grants/delegated", "post"),
        ("/api/authorization-grants/delegated/{grant_id}", "delete"),
    ];
    for (path, method) in operations {
        let operation = &api["paths"][path][method];
        assert!(operation.is_object(), "missing OpenAPI operation {method} {path}");
        let security = operation["security"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|entry| entry.as_object().unwrap().keys().cloned())
            .collect::<BTreeSet<_>>();
        assert_eq!(
            security,
            ["bearerAuth", "webSessionAuth"]
                .into_iter()
                .map(str::to_owned)
                .collect(),
            "administration OpenAPI must advertise only Phase-1 Bearer/Web Session authentication"
        );
        assert!(operation["description"]
            .as_str()
            .unwrap()
            .contains("Participant-HMAC mutation is unsupported in Phase 1"));
    }

    assert_eq!(
        names(
            &api,
            "/components/schemas/DurableAuthorizationGrantCreateRequest/properties"
        ),
        [
            "principal_provider",
            "principal_subject",
            "participant_id",
            "capability",
            "resource",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/DelegatedAuthorizationGrantCreateRequest/properties"
        ),
        [
            "principal_provider",
            "principal_subject",
            "participant_id",
            "capability",
            "resource",
            "intent_id",
            "expires_at",
            "one_shot",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationGrantMutationOutcome/properties"
        ),
        ["store", "id", "state", "rows_changed"]
            .into_iter()
            .map(str::to_owned)
            .collect()
    );

    let mcp = include_str!("mcp.rs");
    for forbidden in [
        "create_durable_grant_authorized(",
        "deactivate_durable_grant_authorized(",
        "create_delegated_grant_authorized(",
        "deactivate_delegated_grant_authorized(",
    ] {
        assert!(
            !mcp.contains(forbidden),
            "#108 is REST-only; MCP administration is a non-goal: {forbidden}"
        );
    }
}

'''
parity = parity.replace(marker, parity_test + marker, 1)
PARITY.write_text(parity)

admin = ADMIN.read_text()
marker = '''    #[test]\n    fn failed_provenance_insert_rolls_back_policy_mutation() {'''
assert marker in admin
rollback_test = r'''    #[test]
    fn authorized_remote_provenance_failure_rolls_back_policy_mutation() {
        let (_dir, conn) = setup();
        identity::provision_web_participant_identity(
            &conn,
            "admin-main",
            "admin",
            Some("Administrator"),
        )
        .unwrap()
        .unwrap();
        identity::set_web_participant_role(&conn, "admin-main", "admin").unwrap();
        conn.execute_batch(
            "CREATE TRIGGER reject_remote_authorization_admin_event
             BEFORE INSERT ON authorization_admin_events
             BEGIN
                 SELECT RAISE(ABORT, 'forced remote administration provenance failure');
             END;",
        )
        .unwrap();
        let principal = Principal {
            provider: "human-web".to_owned(),
            subject: "admin-main".to_owned(),
        };
        let request = DurableGrantCreateRequest {
            principal_provider: "oidc:https://issuer.example",
            principal_subject: "remote-rollback-agent",
            participant_id: "maker-main",
            capability: authorization::READ_MESSAGES,
            resource: None,
        };
        assert!(create_durable_grant_authorized(
            &conn,
            "rest-test",
            &principal,
            "admin-main",
            &request,
        )
        .is_err());
        let grants: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM principal_grants
                 WHERE principal_subject = 'remote-rollback-agent'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(grants, 0);
        assert_eq!(event_count(&conn), 0);
    }

'''
admin = admin.replace(marker, rollback_test + marker, 1)
ADMIN.write_text(admin)

readme = README.read_text()
old = '''Authorization administration is a separate local operator boundary. Durable/delegated create, durable reactivation, and durable/delegated deactivation pass through one canonical administration service and commit non-secret administration provenance atomically with effective policy changes. `grant history` reads that provenance observationally. Administration is not currently exposed as a REST or MCP mutation surface.'''
new = '''Authorization administration is a separate privileged boundary. Durable/delegated create, durable reactivation, and durable/delegated deactivation pass through one canonical administration service and commit non-secret administration provenance atomically with effective policy changes. Local operator CLI and the Phase-1 REST administration projection reuse that service; REST mutation requires the dedicated `manage_authorization_policy` capability on `authorization-policy-administration`. Human Web admin self has narrow implicit administration authority, external Bearer principals require an explicit durable administration grant, and participant-HMAC mutation is unsupported in Phase 1 because the current HTTP proof does not bind JSON mutation bodies. `grant history` reads provenance observationally. MCP grant-administration tools remain out of scope.'''
assert old in readme
README.write_text(readme.replace(old, new, 1))

policy = POLICY.read_text()
old = '''The supported mutation path is:\n\n```text\nlocal operator CLI\n        ↓\nauthorization_admin canonical service\n        ↓\nvalidated durable/delegated lifecycle mutation\n        +\nappend-only authorization_admin_events provenance\n        ↓\none SQLite transaction\n```'''
new = '''The supported mutation paths converge on one domain boundary:\n\n```text\nlocal operator CLI             privileged REST administration\n        \\                            /\n         \\                          /\n          authorization_admin canonical service\n                         ↓\n          validated durable/delegated lifecycle mutation\n                         +\n          append-only authorization_admin_events provenance\n                         ↓\n                 one SQLite transaction\n```\n\nRemote REST administration uses the dedicated `manage_authorization_policy` capability on the global `authorization-policy-administration` resource. Human Web admin self has narrow implicit authority. External Bearer principals require an explicit durable administration grant. Participant-HMAC mutation is deliberately unsupported in Phase 1 because its current HTTP proof binds the request target, not the JSON mutation body.'''
assert old in policy
policy = policy.replace(old, new, 1)
old = '''`authorization_admin::read_administration_events(conn, ...)` is the canonical local provenance reader. `conversation-blackboard grant history` is its operator projection. REST and MCP have no grant mutation endpoint/tool; source regressions protect that boundary so remote adapters cannot silently become a second administration implementation.'''
new = '''`authorization_admin::read_administration_events(conn, ...)` is the canonical local provenance reader. `conversation-blackboard grant history` is its operator projection. Phase-1 REST exposes durable/delegated create and deactivation as thin authenticated projections over authorized `authorization_admin` entry points; durable create also preserves exact-scope existing/reactivation semantics. The REST adapter contains no grant lifecycle SQL and never performs schema repair. MCP grant-administration tools remain a non-goal. Source regressions prevent REST/MCP adapters from calling raw lifecycle entry points or mutating grant tables directly.'''
assert old in policy
POLICY.write_text(policy.replace(old, new, 1))

projections = PROJECTIONS.read_text()
marker = '## Generation and validation rule\n'
assert marker in projections
paragraph = '''Authorization administration is intentionally narrower than the read-side projection set. Phase-1 mutation is REST-only and is documented in OpenAPI for durable create/reactivation/deactivation and delegated create/deactivation. Those HTTP operations are thin projections over authorized `authorization_admin` entry points; OpenAPI advertises Bearer and Human Web session authentication, while participant-HMAC JSON mutation is unsupported in Phase 1. UTCP/MCP grant-administration tools are not introduced by this phase. Parity tests therefore verify the REST operations and schemas while also guarding against accidental MCP mutation projection.\n\n'''
PROJECTIONS.write_text(projections.replace(marker, paragraph + marker, 1))
