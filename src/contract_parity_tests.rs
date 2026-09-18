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
    assert_eq!(utcp["utcp_version"], "1.1.4");
    assert!(utcp.get("contract_projection").is_none());
    let tools = utcp["tools"].as_array().unwrap();
    assert!(!tools.is_empty());
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
    assert!(tools.iter().any(|tool| tool["name"] == "execution_audit"));
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

#[test]
fn execution_audit_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert_eq!(
        names(&api, "/components/schemas/ExecutionAuditBundle/properties"),
        names(
            &contract_schema::execution_audit_bundle_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationProvenance/properties"
        ),
        names(
            &contract_schema::authorization_provenance_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(&api, "/components/schemas/IngressAuditRecord/properties"),
        names(
            &contract_schema::ingress_audit_record_schema(),
            "/properties"
        )
    );
    assert!(api["paths"]
        .get("/api/executions/{intent_id}/audit")
        .is_some());
    let audit = contract_schema::execution_audit_bundle_schema();
    assert!(audit["properties"]["receipt"]["properties"]
        .get("delivery_id")
        .is_none());
    assert!(audit["properties"]["ingress"]["items"]["properties"]
        .get("delivery_id")
        .is_some());
}

#[test]
fn execution_audit_integrity_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert_eq!(
        names(
            &api,
            "/components/schemas/ExecutionAuditIntegrityReport/properties"
        ),
        names(
            &contract_schema::execution_audit_integrity_schema(),
            "/properties"
        )
    );
    assert!(api["paths"]
        .get("/api/executions/{intent_id}/audit/integrity")
        .is_some());
    let utcp = utcp_json();
    assert!(utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .any(|tool| tool["name"] == "execution_audit_integrity"));
}

#[test]
fn execution_audit_sweep_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert_eq!(
        names(
            &api,
            "/components/schemas/ExecutionAuditOrphanEvidence/properties"
        ),
        names(
            &contract_schema::execution_audit_orphan_evidence_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/ExecutionAuditSweepReport/properties"
        ),
        names(
            &contract_schema::execution_audit_sweep_schema(),
            "/properties"
        )
    );
    assert!(api["paths"].get("/api/execution-audit/sweep").is_some());

    let utcp = utcp_json();
    let sweep = utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "execution_audit_sweep")
        .expect("UTCP sweep projection must exist");
    assert_eq!(
        names(sweep, "/outputs/properties/sweep/properties"),
        names(
            &contract_schema::execution_audit_sweep_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            sweep,
            "/outputs/properties/sweep/properties/orphan_evidence/items/properties"
        ),
        names(
            &contract_schema::execution_audit_orphan_evidence_schema(),
            "/properties"
        )
    );
    assert_eq!(
        sweep["tool_call_template"]["url"],
        "${BLACKBOARD_URL}/api/execution-audit/sweep"
    );
}

#[test]
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
        [
            "principal_provider",
            "principal_subject",
            "participant_id",
            "capability"
        ]
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

#[test]
fn authorization_policy_integrity_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert!(api["paths"]
        .get("/api/authorization-policy/integrity")
        .is_some());
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationIntegrityReport/properties"
        ),
        names(
            &contract_schema::authorization_integrity_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationIntegrityViolation/properties"
        ),
        names(
            &contract_schema::authorization_integrity_violation_schema(),
            "/properties"
        )
    );

    let utcp = utcp_json();
    let policy = utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "authorization_policy_integrity")
        .expect("UTCP authorization-policy integrity projection must exist");
    assert_eq!(
        names(policy, "/outputs/properties/integrity/properties"),
        names(
            &contract_schema::authorization_integrity_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            policy,
            "/outputs/properties/integrity/properties/violations/items/properties"
        ),
        names(
            &contract_schema::authorization_integrity_violation_schema(),
            "/properties"
        )
    );
    assert_eq!(
        policy["tool_call_template"]["url"],
        "${BLACKBOARD_URL}/api/authorization-policy/integrity"
    );
}

#[test]
fn authorization_policy_snapshot_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    assert!(api["paths"].get("/api/authorization-policy").is_some());
    assert_eq!(
        names(&api, "/components/schemas/DurableGrantSnapshot/properties"),
        names(
            &contract_schema::durable_grant_snapshot_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/DelegatedGrantSnapshot/properties"
        ),
        names(
            &contract_schema::delegated_grant_snapshot_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            &api,
            "/components/schemas/AuthorizationPolicySnapshot/properties"
        ),
        names(
            &contract_schema::authorization_policy_snapshot_schema(),
            "/properties"
        )
    );
    assert!(
        api["components"]["schemas"]["DelegatedGrantSnapshot"]["properties"]
            .get("created_at")
            .is_none()
    );
    assert!(
        api["components"]["schemas"]["DelegatedGrantSnapshot"]["properties"]
            .get("updated_at")
            .is_none()
    );

    let utcp = utcp_json();
    let policy = utcp["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "authorization_policy")
        .expect("UTCP authorization policy snapshot projection must exist");
    assert_eq!(
        names(policy, "/outputs/properties/policy/properties"),
        names(
            &contract_schema::authorization_policy_snapshot_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            policy,
            "/outputs/properties/policy/properties/durable_grants/items/properties"
        ),
        names(
            &contract_schema::durable_grant_snapshot_schema(),
            "/properties"
        )
    );
    assert_eq!(
        names(
            policy,
            "/outputs/properties/policy/properties/delegated_grants/items/properties"
        ),
        names(
            &contract_schema::delegated_grant_snapshot_schema(),
            "/properties"
        )
    );
    assert!(
        policy["outputs"]["properties"]["policy"]["properties"]["delegated_grants"]["items"]
            ["properties"]
            .get("created_at")
            .is_none()
    );
    assert_eq!(
        policy["tool_call_template"]["url"],
        "${BLACKBOARD_URL}/api/authorization-policy"
    );
}

#[test]
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
        assert!(
            operation.is_object(),
            "missing OpenAPI operation {method} {path}"
        );
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

#[test]
fn authorization_policy_snapshot_adapters_reuse_canonical_reader_without_grant_sql() {
    let adapters = [
        ("http", include_str!("access_api.rs")),
        ("mcp", include_str!("mcp.rs")),
    ];
    for (name, source) in adapters {
        assert!(
            source.contains("read_authorization_policy_snapshot"),
            "{name} adapter must reuse the canonical authorization snapshot reader"
        );
        for forbidden in [
            "FROM principal_grants",
            "FROM delegated_grants",
            "JOIN principal_grants",
            "JOIN delegated_grants",
        ] {
            assert!(
                !source.contains(forbidden),
                "{name} adapter must not enumerate grant tables directly: {forbidden}"
            );
        }
    }
}

#[test]
fn authorization_decision_adapters_reuse_canonical_evaluator_without_policy_sql() {
    let adapters = [
        ("http", include_str!("access_api.rs")),
        ("mcp", include_str!("mcp.rs")),
    ];
    for (name, source) in adapters {
        assert!(
            source.contains("normalize_authorization_decision_target"),
            "{name} adapter must reuse canonical authorization decision target normalization"
        );
        assert!(
            source.contains("explain_authorization"),
            "{name} adapter must reuse the canonical read-only authorization evaluator"
        );
        for forbidden in [
            "FROM principal_grants",
            "FROM delegated_grants",
            "JOIN principal_grants",
            "JOIN delegated_grants",
            "explicit_durable_grant_match",
            "explicit_resource_scope_mismatch",
            "delegated_grant_expired",
            "delegated_intent_mismatch",
            "no_matching_authority",
        ] {
            assert!(
                !source.contains(forbidden),
                "{name} adapter must not reconstruct authorization policy/reason logic: {forbidden}"
            );
        }
    }
}

#[test]
fn authorization_administration_history_contracts_match_canonical_rust_shape() {
    let api = yaml_json();
    let operation = &api["paths"]["/api/authorization-administration/history"]["get"];
    assert!(operation.is_object());
    assert_eq!(
        operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
        "#/components/schemas/AuthorizationAdministrationHistoryEnvelope"
    );
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
            source.contains("read_administration_event_window"),
            "{name} adapter must reuse the canonical bounded administration history reader"
        );
        assert!(
            !source.contains("FROM authorization_admin_events"),
            "{name} adapter must not enumerate authorization administration events directly"
        );
    }
}
