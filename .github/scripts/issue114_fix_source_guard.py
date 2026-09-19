from pathlib import Path

Path("tests/access_context_readonly_contract.rs").write_text(r'''fn function_source<'a>(source: &'a str, signature: &str) -> &'a str {
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
''')
