from pathlib import Path
import json


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    end_index = text.find(end, start_index + len(start))
    if start_index < 0 or end_index < 0:
        raise RuntimeError(f"{label}: boundary not found")
    return text[:start_index] + replacement + text[end_index:]


# Canonical external contract: bounded selected-store policy window.
path = Path('src/contract_schema.rs')
text = path.read_text()
old = '''pub fn authorization_policy_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"policy": authorization_policy_snapshot_schema()},
        "required": ["policy"],
        "additionalProperties": false
    })
}'''
new = '''pub fn authorization_policy_window_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "store": {"type": "string", "enum": ["durable", "delegated"]},
            "durable_grants": {"type": "array", "items": durable_grant_snapshot_schema()},
            "delegated_grants": {"type": "array", "items": delegated_grant_snapshot_schema()},
            "order": {"type": "string", "enum": ["desc", "asc"]},
            "has_more": {"type": "boolean"}
        },
        "required": ["store", "durable_grants", "delegated_grants", "order", "has_more"],
        "additionalProperties": false,
        "description": "Canonical bounded privileged read-only window over one explicit Blackboard authorization grant store."
    })
}

pub fn authorization_policy_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"policy": authorization_policy_window_schema()},
        "required": ["policy"],
        "additionalProperties": false
    })
}'''
text = replace_once(text, old, new, 'policy window schema')
path.write_text(text)


# REST projection: required store selector + canonical keyset window.
path = Path('src/access_api.rs')
text = path.read_text()
start = 'async fn authorization_policy(\n'
end = 'async fn authorization_policy_integrity(\n'
replacement = '''#[derive(Debug, Deserialize)]
struct AuthorizationPolicyQuery {
    store: Option<String>,
    before: Option<i64>,
    after: Option<i64>,
    limit: Option<usize>,
    order: Option<String>,
}

async fn authorization_policy(
    State(state): State<AppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<AuthorizationPolicyQuery>,
) -> Result<Response, AccessApiError> {
    // Authentication deliberately sees the complete path+query so participant-HMAC
    // callers bind store/cursor/window selection to the request target.
    let resolved = require_identity_for_target(&state, &headers, "GET", &uri).await?;
    let principal = principal_for_headers(&headers, &resolved);
    let participant_id = resolved.instance.clone();

    let store = query
        .store
        .filter(|value| authorization::AuthorizationPolicyStore::parse(value).is_some())
        .ok_or_else(|| AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_policy_window"))?;
    let order = authorization::AuthorizationPolicyWindowOrder::parse(query.order.as_deref())
        .ok_or_else(|| AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_policy_window"))?;
    if query.before.is_some_and(|value| value <= 0)
        || query.after.is_some_and(|value| value <= 0)
        || (query.before.is_some() && query.after.is_some())
        || (order == authorization::AuthorizationPolicyWindowOrder::Desc && query.after.is_some())
        || (order == authorization::AuthorizationPolicyWindowOrder::Asc && query.before.is_some())
        || query.limit.is_some_and(|limit| {
            !(1..=authorization::MAX_AUTHORIZATION_POLICY_WINDOW_SIZE).contains(&limit)
        })
    {
        return Err(AccessApiError::new(
            StatusCode::BAD_REQUEST,
            "invalid_policy_window",
        ));
    }
    let before = query.before;
    let after = query.after;
    let limit = query.limit;
    let order_name = query.order.clone();

    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let (allowed, schema_current, window) = with_db_read_only(&state, move |conn| {
        let schema_current = authorization::authorization_policy_snapshot_schema_current(conn)?;
        if !schema_current {
            return Ok((false, false, None));
        }
        let decision = authorization::explain_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY,
            Some(authorization::AUTHORIZATION_POLICY_RESOURCE),
            None,
        )?;
        if !decision.allowed {
            return Ok((false, true, None));
        }
        Ok((
            true,
            true,
            Some(authorization::read_authorization_policy_window(
                conn,
                authorization::AuthorizationPolicyWindowRequest {
                    store: &store,
                    before,
                    after,
                    limit,
                    order: order_name.as_deref(),
                },
            )?),
        ))
    })
    .await?;
    if !schema_current {
        return Err(AccessApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "authorization_schema_not_current",
        ));
    }
    if !allowed {
        return Err(AccessApiError::new(StatusCode::FORBIDDEN, "forbidden"));
    }
    Ok(json_response(
        StatusCode::OK,
        json!({"policy": window.expect("authorized current-schema policy read must produce a window")}),
    ))
}

'''
text = replace_between(text, start, end, replacement, 'REST policy handler')
path.write_text(text)


# MCP projection: same selected-store window, same authority, read-only storage.
path = Path('src/mcp.rs')
text = path.read_text()
old = '''            {
                "name": "blackboard_authorization_policy",
                "title": "Read Blackboard Authorization Policy",
                "description": "Read the privileged canonical inventory of explicit durable and delegated authorization objects without modifying, normalizing, repairing, or consuming authority.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::authorization_policy_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            },'''
new = '''            {
                "name": "blackboard_authorization_policy",
                "title": "Read Blackboard Authorization Policy Window",
                "description": "Read one bounded selected-store window of explicit Blackboard authorization objects without modifying, normalizing, repairing, or consuming authority.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "participant_id": contract_schema::participant_id_schema(),
                        "store": {"type": "string", "enum": ["durable", "delegated"]},
                        "before": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
                        "after": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": authorization::MAX_AUTHORIZATION_POLICY_WINDOW_SIZE, "default": authorization::DEFAULT_AUTHORIZATION_POLICY_WINDOW_SIZE},
                        "order": {"type": "string", "enum": ["desc", "asc"], "default": "desc"},
                        "auth": auth_schema()
                    },
                    "required": ["participant_id", "store", "auth"],
                    "additionalProperties": false
                },
                "outputSchema": contract_schema::authorization_policy_envelope_schema(),
                "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
            },'''
text = replace_once(text, old, new, 'MCP policy tool schema')
start = 'async fn blackboard_authorization_policy(\n'
end = 'async fn blackboard_authorization_policy_integrity(\n'
replacement = '''async fn blackboard_authorization_policy(
    state: &AppState,
    arguments: &Map<String, Value>,
    transport_principal: Option<&execution::Principal>,
) -> Value {
    if !only_keys(
        arguments,
        &["participant_id", "store", "before", "after", "limit", "order", "auth"],
    ) {
        return tool_error("invalid_arguments");
    }
    let participant_id = match arguments.get("participant_id").and_then(Value::as_str) {
        Some(value) => match identity::validate_participant_id(value) {
            Some(normalized) if normalized == value => normalized,
            _ => return tool_error("invalid_participant_id"),
        },
        None => return tool_error("invalid_participant_id"),
    };
    let store = match arguments.get("store").and_then(Value::as_str) {
        Some(value) if authorization::AuthorizationPolicyStore::parse(value).is_some() => {
            value.to_owned()
        }
        _ => return tool_error("invalid_policy_window"),
    };
    let before = match arguments.get("before") {
        None | Some(Value::Null) => None,
        Some(Value::Number(value)) => value.as_i64().filter(|value| *value > 0),
        Some(_) => return tool_error("invalid_policy_window"),
    };
    if arguments.contains_key("before")
        && !arguments
            .get("before")
            .is_some_and(|value| value.is_null() || before.is_some())
    {
        return tool_error("invalid_policy_window");
    }
    let after = match arguments.get("after") {
        None | Some(Value::Null) => None,
        Some(Value::Number(value)) => value.as_i64().filter(|value| *value > 0),
        Some(_) => return tool_error("invalid_policy_window"),
    };
    if arguments.contains_key("after")
        && !arguments
            .get("after")
            .is_some_and(|value| value.is_null() || after.is_some())
    {
        return tool_error("invalid_policy_window");
    }
    let limit = match arguments.get("limit") {
        None => None,
        Some(Value::Number(value)) => {
            match value.as_u64().and_then(|value| usize::try_from(value).ok()) {
                Some(value)
                    if (1..=authorization::MAX_AUTHORIZATION_POLICY_WINDOW_SIZE)
                        .contains(&value) =>
                {
                    Some(value)
                }
                _ => return tool_error("invalid_policy_window"),
            }
        }
        Some(_) => return tool_error("invalid_policy_window"),
    };
    let order = match arguments.get("order") {
        None => None,
        Some(Value::String(value))
            if authorization::AuthorizationPolicyWindowOrder::parse(Some(value)).is_some() =>
        {
            Some(value.clone())
        }
        Some(_) => return tool_error("invalid_policy_window"),
    };
    let parsed_order = authorization::AuthorizationPolicyWindowOrder::parse(order.as_deref())
        .expect("validated policy window order");
    if before.is_some() && after.is_some()
        || (parsed_order == authorization::AuthorizationPolicyWindowOrder::Desc && after.is_some())
        || (parsed_order == authorization::AuthorizationPolicyWindowOrder::Asc && before.is_some())
    {
        return tool_error("invalid_policy_window");
    }

    // Authenticate first without evaluating policy. A verified Bearer principal
    // has precedence; otherwise preserve participant-HMAC capability proof.
    let principal = if let Some(principal) = transport_principal {
        principal.clone()
    } else {
        if let Err(code) = resolve_capability_identity(
            state,
            arguments,
            &participant_id,
            authorization::READ_AUTHORIZATION_POLICY,
            Some(authorization::AUTHORIZATION_POLICY_RESOURCE),
        )
        .await
        {
            return tool_error(code);
        }
        execution::Principal {
            provider: "participant-hmac".to_owned(),
            subject: participant_id.clone(),
        }
    };

    let policy_principal = principal.clone();
    let lookup_participant = participant_id.clone();
    let window_order = order.clone();
    let (schema_current, allowed, window) = match with_db_read_only(state, move |conn| {
        let schema_current = authorization::authorization_policy_snapshot_schema_current(conn)?;
        if !schema_current {
            return Ok((false, false, None));
        }
        let decision = authorization::explain_authorization(
            conn,
            &policy_principal,
            &lookup_participant,
            authorization::READ_AUTHORIZATION_POLICY,
            Some(authorization::AUTHORIZATION_POLICY_RESOURCE),
            None,
        )?;
        if !decision.allowed {
            return Ok((true, false, None));
        }
        Ok((
            true,
            true,
            Some(authorization::read_authorization_policy_window(
                conn,
                authorization::AuthorizationPolicyWindowRequest {
                    store: &store,
                    before,
                    after,
                    limit,
                    order: window_order.as_deref(),
                },
            )?),
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
    tool_success(json!({
        "policy": window.expect("authorized current-schema policy read must produce a window")
    }))
}

'''
text = replace_between(text, start, end, replacement, 'MCP policy handler')
path.write_text(text)


# OpenAPI projection.
path = Path('integrations/openapi.yaml')
text = path.read_text()
old = '''  /api/authorization-policy:
    get:
      operationId: blackboardAuthorizationPolicy
      summary: Read the explicit Blackboard authorization policy inventory.
      description: Privileged projection of the canonical read-only authorization policy snapshot. The response includes durable and delegated grant lifecycle state without creating, repairing, normalizing, reactivating, deactivating, or consuming authority. Legacy authorization schemas fail without migration.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      responses:
        '200':
          description: Canonical explicit authorization policy snapshot.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationPolicyEnvelope'
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
new = '''  /api/authorization-policy:
    get:
      operationId: blackboardAuthorizationPolicy
      summary: Read one bounded explicit Blackboard authorization policy window.
      description: Privileged projection of the canonical selected-store authorization policy window. Exactly one of the durable or delegated grant arrays is populated. Pagination changes observation selection only; read_authorization_policy authority remains global and unchanged. The read never creates, repairs, normalizes, reactivates, deactivates, or consumes authority, and legacy authorization schemas fail without migration.
      security:
        - bearerAuth: []
        - webSessionAuth: []
      parameters:
        - name: store
          in: query
          required: true
          description: Select the independent durable or delegated grant ID domain to traverse.
          schema:
            type: string
            enum: [durable, delegated]
        - name: order
          in: query
          required: false
          schema:
            type: string
            enum: [desc, asc]
            default: desc
        - name: before
          in: query
          required: false
          description: Exclusive grant-id upper cursor used only with order=desc within the selected store.
          schema:
            type: integer
            minimum: 1
        - name: after
          in: query
          required: false
          description: Exclusive grant-id lower cursor used only with order=asc within the selected store.
          schema:
            type: integer
            minimum: 1
        - name: limit
          in: query
          required: false
          schema:
            type: integer
            minimum: 1
            maximum: 200
            default: 20
      responses:
        '200':
          description: Canonical bounded selected-store authorization policy window.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AuthorizationPolicyEnvelope'
        '400':
          description: Missing or invalid store, order, cursor, or window limit.
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
text = replace_once(text, old, new, 'OpenAPI policy endpoint')
old = '''    AuthorizationPolicySnapshot:
      type: object
      additionalProperties: false
      required: [durable_grants, delegated_grants]
      description: Canonical privileged read-only inventory of explicit Blackboard authorization objects.
      properties:
        durable_grants:
          type: array
          items:
            $ref: '#/components/schemas/DurableGrantSnapshot'
        delegated_grants:
          type: array
          items:
            $ref: '#/components/schemas/DelegatedGrantSnapshot'

    AuthorizationPolicyEnvelope:
      type: object
      additionalProperties: false
      required: [policy]
      properties:
        policy:
          $ref: '#/components/schemas/AuthorizationPolicySnapshot'
'''
new = '''    AuthorizationPolicyWindow:
      type: object
      additionalProperties: false
      required: [store, durable_grants, delegated_grants, order, has_more]
      description: Canonical bounded privileged read-only window over one explicit Blackboard authorization grant store.
      properties:
        store:
          type: string
          enum: [durable, delegated]
        durable_grants:
          type: array
          items:
            $ref: '#/components/schemas/DurableGrantSnapshot'
        delegated_grants:
          type: array
          items:
            $ref: '#/components/schemas/DelegatedGrantSnapshot'
        order:
          type: string
          enum: [desc, asc]
        has_more:
          type: boolean
          description: Whether another page exists in the selected store and traversal direction.

    AuthorizationPolicyEnvelope:
      type: object
      additionalProperties: false
      required: [policy]
      properties:
        policy:
          $ref: '#/components/schemas/AuthorizationPolicyWindow'
'''
text = replace_once(text, old, new, 'OpenAPI policy component')
path.write_text(text)


# UTCP discovery projection.
path = Path('integrations/utcp.json')
data = json.loads(path.read_text())
tool = next(tool for tool in data['tools'] if tool['name'] == 'authorization_policy')
tool['description'] = (
    'Read one bounded selected-store window of explicit Blackboard authorization objects. '
    'Blackboard authorization remains authoritative; pagination changes only observation selection.'
)
tool['inputs']['required'] = ['store']
tool['inputs']['properties'] = {
    'store': {
        'type': 'string',
        'enum': ['durable', 'delegated'],
        'description': 'Select the independent durable or delegated grant ID domain.'
    },
    'before': {
        'type': ['integer', 'null'],
        'minimum': 1,
        'description': 'Exclusive grant-id upper cursor used only with order=desc.'
    },
    'after': {
        'type': ['integer', 'null'],
        'minimum': 1,
        'description': 'Exclusive grant-id lower cursor used only with order=asc.'
    },
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 20},
    'order': {'type': 'string', 'enum': ['desc', 'asc'], 'default': 'desc'},
}
policy = tool['outputs']['properties']['policy']
policy['required'] = ['store', 'durable_grants', 'delegated_grants', 'order', 'has_more']
policy['properties']['store'] = {'type': 'string', 'enum': ['durable', 'delegated']}
policy['properties']['order'] = {'type': 'string', 'enum': ['desc', 'asc']}
policy['properties']['has_more'] = {'type': 'boolean'}
path.write_text(json.dumps(data, indent=2) + '\n')
