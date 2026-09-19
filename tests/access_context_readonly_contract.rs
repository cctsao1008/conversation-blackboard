fn function_source<'a>(source: &'a str, signature: &str) -> &'a str {
    let start = source
        .find(signature)
        .unwrap_or_else(|| panic!("function signature not found: {signature}"));
    let open = start
        + source[start..]
            .find('{')
            .expect("function opening brace must exist");
    let mut depth = 0_i32;
    for (offset, byte) in source[open..].bytes().enumerate() {
        match byte {
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return &source[start..=open + offset];
                }
            }
            _ => {}
        }
    }
    panic!("function closing brace not found: {signature}");
}

#[test]
fn effective_grants_cannot_reenter_schema_mutation_paths() {
    let source = include_str!("../src/authorization.rs");
    let body = function_source(source, "pub fn effective_grants(\n");

    assert!(body.contains("effective_grants_schema_current(conn)?"));
    assert!(body.contains("evaluate_authorization_current_schema("));
    assert!(!body.contains("ensure_grant_schema("));
    assert!(!body.contains("authorize("));
}

#[test]
fn access_context_openapi_documents_stale_schema_without_repair() {
    let openapi = include_str!("../integrations/openapi.yaml");
    let start = openapi
        .find("  /api/access-context:\n")
        .expect("access-context OpenAPI path must exist");
    let tail = &openapi[start..];
    let end = tail
        .find("\n  /api/executions/{intent_id}:\n")
        .expect("next OpenAPI path must exist");
    let access_context = &tail[..end];

    assert!(access_context.contains("'503':"));
    assert!(access_context.contains("#/components/schemas/Error"));
    assert!(access_context.contains("does not migrate or repair schema"));
}
