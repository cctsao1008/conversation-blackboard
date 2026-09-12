#[test]
fn back_to_latest_is_contextual_and_refresh_preserves_history() {
    let app = include_str!("../web/app.js");
    let html = include_str!("../web/index.html");

    // The live/latest window does not expose a redundant Latest action.
    assert!(html.contains("id=\"latest\" class=\"secondary hidden\""));
    assert!(html.contains(">Back to latest</button>"));
    assert!(html.contains("id=\"history-state\""));

    // Historical navigation is explicit state, not an alias for sorting.
    assert!(app.contains("historyTargetId: null"));
    assert!(app.contains("const historical = !state.followLatest;"));
    assert!(app.contains("$(\"latest\").classList.toggle(\"hidden\", !historical);"));
    assert!(app.contains("Viewing around #${state.historyTargetId}"));
    assert!(app.contains("state.historyTargetId = id;"));

    // Refreshing a jumped-to historical window reuses its target instead of
    // silently returning to the live window.
    assert!(app.contains("if (state.historyTargetId)"));
    assert!(app.contains("const target = state.historyTargetId;"));
    assert!(app.contains("await jumpToMessage({ preventDefault() {} });"));

    // Returning to live explicitly restores newest-first because polling is
    // defined only for descending windows.
    assert!(app.contains("Live polling is defined only for newest-first windows"));
    assert!(app.contains("state.order = \"desc\";"));
    assert!(app.contains("Use Back to latest to return to the live window."));
}
