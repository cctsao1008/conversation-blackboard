from pathlib import Path
import json


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

# Canonical external schema now describes a bounded history window.
path = Path('src/contract_schema.rs')
text = path.read_text()
text = replace_once(
    text,
    '''        "properties": {\n            "events": {"type": "array", "items": authorization_administration_event_schema()}\n        },\n        "required": ["events"],\n        "additionalProperties": false,\n        "description": "Canonical read-only committed authorization-administration history."''',
    '''        "properties": {\n            "events": {"type": "array", "items": authorization_administration_event_schema()},\n            "order": {"type": "string", "enum": ["desc", "asc"]},\n            "has_more": {"type": "boolean"}\n        },\n        "required": ["events", "order", "has_more"],\n        "additionalProperties": false,\n        "description": "Canonical bounded read-only committed authorization-administration history window."''',
    'history window schema',
)
path.write_text(text)

# REST projection.
path = Path('src/access_api.rs')
text = path.read_text()
text = replace_once(
    text,
    '''struct AuthorizationAdministrationHistoryQuery {\n    participant_id: Option<String>,\n}''',
    '''struct AuthorizationAdministrationHistoryQuery {\n    participant_id: Option<String>,\n    before: Option<i64>,\n    after: Option<i64>,\n    limit: Option<usize>,\n    order: Option<String>,\n}''',
    'REST history query',
)
old = '''    let participant_filter = query\n        .participant_id\n        .map(|value| {\n            identity::validate_participant_id(&value).ok_or_else(|| {\n                AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_participant_id")\n            })\n        })\n        .transpose()?;\n\n    let (schema_current, allowed, events) = with_db_read_only(&state, move |conn| {\n        if !authorization_admin::schema_current(conn)? {\n            return Ok((false, false, Vec::new()));\n        }\n        let decision = authorization::explain_authorization(\n            conn,\n            &principal,\n            &caller_participant_id,\n            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,\n            Some(authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),\n            None,\n        )?;\n        let events = if decision.allowed {\n            authorization_admin::read_administration_events(conn, participant_filter.as_deref())?\n        } else {\n            Vec::new()\n        };\n        Ok((true, decision.allowed, events))\n    })\n    .await?;'''
new = '''    let participant_filter = query\n        .participant_id\n        .map(|value| {\n            identity::validate_participant_id(&value).ok_or_else(|| {\n                AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_participant_id")\n            })\n        })\n        .transpose()?;\n    let order = authorization_admin::AdministrationHistoryOrder::parse(query.order.as_deref())\n        .ok_or_else(|| AccessApiError::new(StatusCode::BAD_REQUEST, "invalid_history_window"))?;\n    if query.before.is_some_and(|value| value <= 0)\n        || query.after.is_some_and(|value| value <= 0)\n        || (query.before.is_some() && query.after.is_some())\n        || (order == authorization_admin::AdministrationHistoryOrder::Desc && query.after.is_some())\n        || (order == authorization_admin::AdministrationHistoryOrder::Asc && query.before.is_some())\n        || query.limit.is_some_and(|limit| {\n            !(1..=authorization_admin::MAX_ADMINISTRATION_HISTORY_WINDOW_SIZE).contains(&limit)\n        })\n    {\n        return Err(AccessApiError::new(\n            StatusCode::BAD_REQUEST,\n            "invalid_history_window",\n        ));\n    }\n    let before = query.before;\n    let after = query.after;\n    let limit = query.limit;\n    let order_name = query.order.clone();\n\n    let (schema_current, allowed, window) = with_db_read_only(&state, move |conn| {\n        if !authorization_admin::schema_current(conn)? {\n            return Ok((false, false, None));\n        }\n        let decision = authorization::explain_authorization(\n            conn,\n            &principal,\n            &caller_participant_id,\n            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,\n            Some(authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),\n            None,\n        )?;\n        let window = if decision.allowed {\n            Some(authorization_admin::read_administration_event_window(\n                conn,\n                authorization_admin::AuthorizationAdministrationHistoryWindowRequest {\n                    participant_id: participant_filter.as_deref(),\n                    before,\n                    after,\n                    limit,\n                    order: order_name.as_deref(),\n                },\n            )?)\n        } else {\n            None\n        };\n        Ok((true, decision.allowed, window))\n    })\n    .await?;'''
text = replace_once(text, old, new, 'REST bounded reader')
text = replace_once(
    text,
    '        json!({"history": {"events": events}}),',
    '        json!({"history": window.expect("authorized history read must produce a window")}),',
    'REST bounded output',
)
path.write_text(text)

# MCP projection and advertised contract.
path = Path('src/mcp.rs')
text = path.read_text()
text = replace_once(
    text,
    '''                        "filter_participant_id": {"anyOf": [contract_schema::participant_id_schema(), {"type": "null"}]},\n                        "auth": auth_schema()''',
    '''                        "filter_participant_id": {"anyOf": [contract_schema::participant_id_schema(), {"type": "null"}]},\n                        "before": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},\n                        "after": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},\n                        "limit": {"type": "integer", "minimum": 1, "maximum": authorization_admin::MAX_ADMINISTRATION_HISTORY_WINDOW_SIZE, "default": authorization_admin::DEFAULT_ADMINISTRATION_HISTORY_WINDOW_SIZE},\n                        "order": {"type": "string", "enum": ["desc", "asc"], "default": "desc"},\n                        "auth": auth_schema()''',
    'MCP history schema inputs',
)
text = replace_once(
    text,
    '&["participant_id", "filter_participant_id", "auth"],',
    '&["participant_id", "filter_participant_id", "before", "after", "limit", "order", "auth"],',
    'MCP history allowed keys',
)
anchor = '''    let filter_participant_id = match arguments.get("filter_participant_id") {\n        None | Some(Value::Null) => None,\n        Some(Value::String(value)) => match identity::validate_participant_id(value) {\n            Some(normalized) if normalized == *value => Some(normalized),\n            _ => return tool_error("invalid_filter_participant_id"),\n        },\n        Some(_) => return tool_error("invalid_filter_participant_id"),\n    };\n'''
insert = anchor + '''    let before = match arguments.get("before") {\n        None | Some(Value::Null) => None,\n        Some(Value::Number(value)) => value.as_i64().filter(|value| *value > 0),\n        Some(_) => return tool_error("invalid_history_window"),\n    };\n    if arguments.contains_key("before") && !arguments.get("before").is_some_and(|value| value.is_null() || before.is_some()) {\n        return tool_error("invalid_history_window");\n    }\n    let after = match arguments.get("after") {\n        None | Some(Value::Null) => None,\n        Some(Value::Number(value)) => value.as_i64().filter(|value| *value > 0),\n        Some(_) => return tool_error("invalid_history_window"),\n    };\n    if arguments.contains_key("after") && !arguments.get("after").is_some_and(|value| value.is_null() || after.is_some()) {\n        return tool_error("invalid_history_window");\n    }\n    let limit = match arguments.get("limit") {\n        None => None,\n        Some(Value::Number(value)) => match value.as_u64().and_then(|value| usize::try_from(value).ok()) {\n            Some(value) if (1..=authorization_admin::MAX_ADMINISTRATION_HISTORY_WINDOW_SIZE).contains(&value) => Some(value),\n            _ => return tool_error("invalid_history_window"),\n        },\n        Some(_) => return tool_error("invalid_history_window"),\n    };\n    let order = match arguments.get("order") {\n        None => None,\n        Some(Value::String(value)) if authorization_admin::AdministrationHistoryOrder::parse(Some(value)).is_some() => Some(value.clone()),\n        Some(_) => return tool_error("invalid_history_window"),\n    };\n    let parsed_order = authorization_admin::AdministrationHistoryOrder::parse(order.as_deref())\n        .expect("validated history order");\n    if before.is_some() && after.is_some()\n        || (parsed_order == authorization_admin::AdministrationHistoryOrder::Desc && after.is_some())\n        || (parsed_order == authorization_admin::AdministrationHistoryOrder::Asc && before.is_some())\n    {\n        return tool_error("invalid_history_window");\n    }\n'''
text = replace_once(text, anchor, insert, 'MCP history parser')
old = '''    let policy_principal = caller_principal.clone();\n    let caller_participant = participant_id.clone();\n    let filter = filter_participant_id.clone();\n    let (schema_current, allowed, events) = match with_db_read_only(state, move |conn| {\n        if !authorization_admin::schema_current(conn)? {\n            return Ok((false, false, Vec::new()));\n        }\n        let decision = authorization::explain_authorization(\n            conn,\n            &policy_principal,\n            &caller_participant,\n            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,\n            Some(authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),\n            None,\n        )?;\n        if !decision.allowed {\n            return Ok((true, false, Vec::new()));\n        }\n        Ok((\n            true,\n            true,\n            authorization_admin::read_administration_events(conn, filter.as_deref())?,\n        ))\n    })'''
new = '''    let policy_principal = caller_principal.clone();\n    let caller_participant = participant_id.clone();\n    let filter = filter_participant_id.clone();\n    let window_order = order.clone();\n    let (schema_current, allowed, window) = match with_db_read_only(state, move |conn| {\n        if !authorization_admin::schema_current(conn)? {\n            return Ok((false, false, None));\n        }\n        let decision = authorization::explain_authorization(\n            conn,\n            &policy_principal,\n            &caller_participant,\n            authorization::READ_AUTHORIZATION_ADMINISTRATION_HISTORY,\n            Some(authorization::AUTHORIZATION_ADMINISTRATION_HISTORY_RESOURCE),\n            None,\n        )?;\n        if !decision.allowed {\n            return Ok((true, false, None));\n        }\n        Ok((\n            true,\n            true,\n            Some(authorization_admin::read_administration_event_window(\n                conn,\n                authorization_admin::AuthorizationAdministrationHistoryWindowRequest {\n                    participant_id: filter.as_deref(),\n                    before,\n                    after,\n                    limit,\n                    order: window_order.as_deref(),\n                },\n            )?),\n        ))\n    })'''
text = replace_once(text, old, new, 'MCP bounded reader')
text = replace_once(
    text,
    '    tool_success(json!({"history": {"events": events}}))',
    '    tool_success(json!({"history": window.expect("authorized history read must produce a window")}))',
    'MCP bounded output',
)
path.write_text(text)

# OpenAPI route parameters and canonical history component.
path = Path('integrations/openapi.yaml')
text = path.read_text()
participant_param = '''        - name: participant_id\n          in: query\n          required: false\n          description: Optional returned-history filter; not an authorization scope.\n          schema:\n            $ref: '#/components/schemas/ParticipantId'\n'''
window_params = participant_param + '''        - name: order\n          in: query\n          required: false\n          schema:\n            type: string\n            enum: [desc, asc]\n            default: desc\n        - name: before\n          in: query\n          required: false\n          description: Exclusive event-id upper cursor used only with order=desc.\n          schema:\n            type: integer\n            minimum: 1\n        - name: after\n          in: query\n          required: false\n          description: Exclusive event-id lower cursor used only with order=asc.\n          schema:\n            type: integer\n            minimum: 1\n        - name: limit\n          in: query\n          required: false\n          schema:\n            type: integer\n            minimum: 1\n            maximum: 200\n            default: 20\n'''
text = replace_once(text, participant_param, window_params, 'OpenAPI history params')
text = replace_once(
    text,
    '          description: Canonical committed authorization-administration history in event-id order.',
    '          description: Canonical bounded authorization-administration history window in requested event-id order.',
    'OpenAPI history response description',
)
text = replace_once(
    text,
    '          description: Invalid participant history filter.',
    '          description: Invalid participant filter, order, cursor, or window limit.',
    'OpenAPI history 400 description',
)
text = replace_once(
    text,
    '''    AuthorizationAdministrationHistory:\n      type: object\n      additionalProperties: false\n      required: [events]\n      description: Canonical read-only committed authorization-administration history.\n      properties:\n        events:\n          type: array\n          items:\n            $ref: '#/components/schemas/AuthorizationAdministrationEvent'\n''',
    '''    AuthorizationAdministrationHistory:\n      type: object\n      additionalProperties: false\n      required: [events, order, has_more]\n      description: Canonical bounded read-only committed authorization-administration history window.\n      properties:\n        events:\n          type: array\n          items:\n            $ref: '#/components/schemas/AuthorizationAdministrationEvent'\n        order:\n          type: string\n          enum: [desc, asc]\n        has_more:\n          type: boolean\n          description: Whether another page exists in the selected traversal direction.\n''',
    'OpenAPI history component',
)
path.write_text(text)

# UTCP is JSON; mutate the one history tool structurally.
path = Path('integrations/utcp.json')
data = json.loads(path.read_text())
tool = next(tool for tool in data['tools'] if tool['name'] == 'authorization_administration_history')
props = tool['inputs']['properties']
props['before'] = {'type': ['integer', 'null'], 'minimum': 1, 'description': 'Exclusive event-id upper cursor used only with order=desc.'}
props['after'] = {'type': ['integer', 'null'], 'minimum': 1, 'description': 'Exclusive event-id lower cursor used only with order=asc.'}
props['limit'] = {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 20}
props['order'] = {'type': 'string', 'enum': ['desc', 'asc'], 'default': 'desc'}
history = tool['outputs']['properties']['history']
history['required'] = ['events', 'order', 'has_more']
history['properties']['order'] = {'type': 'string', 'enum': ['desc', 'asc']}
history['properties']['has_more'] = {'type': 'boolean'}
tool['description'] = 'Read a bounded immutable privileged authorization-administration provenance window through the canonical read-only history surface. participant_id only filters returned history; cursors select observation windows and never alter reader authority.'
path.write_text(json.dumps(data, indent=2) + '\n')
