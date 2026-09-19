#[test]
fn back_to_latest_is_contextual_and_refresh_preserves_history() {
    let app = include_str!("../web/app.js");
    let html = include_str!("../web/index.html");

    // The live/latest window does not expose a redundant Latest action.
    assert!(html.contains("id=\"latest\" class=\"secondary hidden\""));
    assert!(html.contains(">Back to latest</button>"));
    assert!(html.contains("id=\"history-state\""));

    // The jump control states its current-channel scope explicitly.
    assert!(html.contains(">Jump in channel to #</label>"));

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

#[test]
fn channel_directory_browser_consumption_is_explicitly_bounded() {
    let app = include_str!("../web/app.js");
    let html = include_str!("../web/index.html");

    assert!(app.contains("const CHANNEL_DIRECTORY_PAGE_SIZE = 20;"));
    assert!(app.contains("channelDirectoryHasMore: false"));
    assert!(app.contains("adminChannelHasMore: false"));
    assert!(app.contains("after_name=${encodeURIComponent(afterName)}"));
    assert!(app.contains("async function loadMoreChannels()"));
    assert!(app.contains("async function loadMoreAdminChannels()"));
    assert!(html.contains("id=\"load-more-channels\""));
    assert!(html.contains("id=\"load-more-admin-channels\""));

    // Selecting a channel and live message polling must not turn the bounded
    // directory endpoint back into a periodic full-directory refresh.
    assert!(app.contains("async function selectChannel(channel)"));
    assert!(!app.contains("refreshChannelCounts"));
    assert!(app.contains("applyCurrentChannelActivity(data.messages, insertedCount);"));
    assert!(app.contains("applyCurrentChannelActivity([data.message], 1);"));
}

#[test]
fn channel_directory_remote_source_boundary_stays_bounded() {
    let db = include_str!("../src/db.rs");
    let http = include_str!("../src/http.rs");

    // The retired full-corpus readers must not be reintroduced. Remote
    // directory traversal has one canonical bounded DB primitive.
    assert!(!db.contains("pub fn list_channels("));
    assert!(!db.contains("pub fn list_public_channels("));
    assert!(!db.contains("fn collect_channel_directory("));
    assert!(db.contains("pub fn read_channel_directory_window("));

    // HTTP is a projection only: it must call the canonical window reader and
    // must not own channel/message aggregation SQL.
    assert!(http.contains("db::read_channel_directory_window("));
    assert!(!http.contains("FROM channels c"));
    assert!(!http.contains("COUNT(m.id) AS message_count"));
}
