from pathlib import Path

openapi_path = Path('integrations/openapi.yaml')
openapi = openapi_path.read_text()
old = '''  /api/access-context:
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
'''
new = '''  /api/access-context:
    get:
      operationId: blackboardAccessContext
      summary: Read the authenticated caller's effective Blackboard access context.
      description: HTTP projection of the shared authorization kernel. The read refuses stale authorization schema and never repairs it.
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
        '503':
          description: Authorization schema is not current or storage is unavailable. The access-context read does not migrate or repair schema.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
'''
if old not in openapi:
    raise RuntimeError('access-context OpenAPI anchor not found')
openapi_path.write_text(openapi.replace(old, new, 1))

parity_path = Path('src/contract_parity_tests.rs')
parity = parity_path.read_text()
marker = 'fn access_context_openapi_declares_stale_schema_error_without_utcp_error_fork()'
if marker not in parity:
    parity += '''\n#[test]\nfn access_context_openapi_declares_stale_schema_error_without_utcp_error_fork() {\n    let api = yaml_json();\n    let access_context = &api["paths"]["/api/access-context"]["get"];\n    assert!(access_context.is_object());\n    assert!(access_context["description"]\n        .as_str()\n        .unwrap()\n        .contains("never repairs"));\n    assert_eq!(\n        access_context["responses"]["503"]["content"]["application/json"]["schema"]["$ref"],\n        "#/components/schemas/Error"\n    );\n\n    let utcp = utcp_json();\n    let tool = utcp["tools"]\n        .as_array()\n        .unwrap()\n        .iter()\n        .find(|tool| tool["name"] == "access_context")\n        .expect("UTCP access_context projection must remain present");\n    assert_eq!(\n        tool["tool_call_template"]["url"],\n        "${BLACKBOARD_URL}/api/access-context"\n    );\n    assert!(tool.get("errors").is_none());\n}\n'''
    parity_path.write_text(parity)
