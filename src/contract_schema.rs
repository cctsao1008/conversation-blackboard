#![cfg_attr(not(test), allow(dead_code))]

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

pub fn authorization_provenance_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "participant_id": participant_id_schema(),
            "intent_id": intent_id_schema(),
            "source": {"type": "string"},
            "reason": {"type": "string"},
            "grant_id": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
        },
        "required": ["participant_id", "intent_id", "source", "reason", "grant_id"],
        "additionalProperties": false,
        "description": "Historical non-secret authorization decision persisted at execution commit time."
    })
}

pub fn ingress_audit_record_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "delivery_id": delivery_id_schema(),
            "intent_id": intent_id_schema(),
            "transport": {"type": "string"},
            "external_ref": {"type": "string"},
            "principal": principal_schema()
        },
        "required": ["delivery_id", "intent_id", "transport", "external_ref", "principal"],
        "additionalProperties": false
    })
}

pub fn execution_audit_bundle_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "receipt": execution_receipt_schema(),
            "authorization": {
                "anyOf": [authorization_provenance_schema(), {"type": "null"}]
            },
            "ingress": {
                "type": "array",
                "items": ingress_audit_record_schema()
            }
        },
        "required": ["receipt", "authorization", "ingress"],
        "additionalProperties": false,
        "description": "Immutable historical execution read model assembled from committed receipt, authorization provenance, and ingress provenance."
    })
}

pub fn execution_audit_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"audit": execution_audit_bundle_schema()},
        "required": ["audit"],
        "additionalProperties": false
    })
}

pub fn execution_audit_integrity_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "participant_id": participant_id_schema(),
            "intent_id": intent_id_schema(),
            "valid": {"type": "boolean"},
            "checks": {"type": "array", "items": {"type": "string"}},
            "violations": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["participant_id", "intent_id", "valid", "checks", "violations"],
        "additionalProperties": false,
        "description": "Read-only structural integrity report over committed execution evidence. It does not re-evaluate historical authorization policy."
    })
}

pub fn execution_audit_integrity_envelope_schema() -> Value {
    json!({
        "type": "object",
        "properties": {"integrity": execution_audit_integrity_schema()},
        "required": ["integrity"],
        "additionalProperties": false
    })
}
