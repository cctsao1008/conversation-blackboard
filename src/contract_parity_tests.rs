use std::collections::BTreeSet;

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
    let yaml: serde_yaml::Value =
        serde_yaml::from_str(include_str!("../integrations/openapi.yaml"))
            .expect("openapi.yaml must parse");
    serde_json::to_value(yaml).expect("OpenAPI YAML must convert to JSON value")
}

fn utcp_json() -> Value {
    serde_json::from_str(include_str!("../integrations/utcp.json")).expect("utcp.json must parse")
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
    assert_eq!(
        utcp["contract_projection"]["authority"],
        "rust-application-kernel"
    );
    let tools = utcp["tools"].as_array().unwrap();
    let read = tools
        .iter()
        .find(|tool| tool["name"] == "read_messages")
        .unwrap();
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
        .to_ascii_lowercase()
        .contains("transport"));
}
