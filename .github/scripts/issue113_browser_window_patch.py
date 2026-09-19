from pathlib import Path

app = Path('web/app.js')
text = app.read_text()

text = text.replace(
'''  const HISTORY_PAGE_SIZE = 20;\n''',
'''  const HISTORY_PAGE_SIZE = 20;\n  const CHANNEL_DIRECTORY_PAGE_SIZE = 20;\n''',
1,
)

text = text.replace(
'''    channels: [],\n    lastId: 0,\n''',
'''    channels: [],\n    channelDirectoryAfterName: "",\n    channelDirectoryHasMore: false,\n    adminChannels: [],\n    adminChannelAfterName: "",\n    adminChannelHasMore: false,\n    lastId: 0,\n''',
1,
)

text = text.replace(
'''    state.channels = [];\n    state.replyTo = null;\n''',
'''    state.channels = [];\n    state.channelDirectoryAfterName = "";\n    state.channelDirectoryHasMore = false;\n    state.adminChannels = [];\n    state.adminChannelAfterName = "";\n    state.adminChannelHasMore = false;\n    state.replyTo = null;\n''',
1,
)

old = '''  function renderChannels(channels) {\n    state.channels = channels;\n    const publicChannels = channels.filter((channel) => channel.status === "active" && channel.visibility === "public");\n    const privateChannels = channels.filter((channel) => channel.status === "active" && channel.visibility === "private");\n    const archivedChannels = channels.filter((channel) => channel.status === "archived");\n    const groups = [\n      channelGroup("PUBLIC", publicChannels),\n      channelGroup("PRIVATE", privateChannels),\n      channelGroup("ARCHIVED", archivedChannels),\n    ].filter(Boolean);\n    $("channels").replaceChildren(...groups);\n    updateChannelHeader();\n    updateCapabilityUi();\n  }\n\n  async function loadChannels() {\n    try {\n      const data = await api("/api/channels");\n      renderChannels(data.channels || []);\n\n      const selectedChannelExists = state.channels.some((channel) => channel.channel === state.channel);\n      if (!selectedChannelExists) {\n        const preferred = state.channels.find((channel) => channel.status === "active") || state.channels[0];\n        state.channel = preferred ? preferred.channel : "";\n      }\n      if (state.channel) {\n        await selectChannel(state.channel, false);\n      } else {\n        $("channel-name").textContent = "No channels available";\n        resetHistory();\n      }\n    } catch (error) {\n      if (error.status === 401) disconnect("Session is no longer valid.");\n      else throw error;\n    }\n  }\n'''
new = '''  function renderChannels(channels = state.channels) {\n    state.channels = channels;\n    const publicChannels = channels.filter((channel) => channel.status === "active" && channel.visibility === "public");\n    const privateChannels = channels.filter((channel) => channel.status === "active" && channel.visibility === "private");\n    const archivedChannels = channels.filter((channel) => channel.status === "archived");\n    const groups = [\n      channelGroup("PUBLIC", publicChannels),\n      channelGroup("PRIVATE", privateChannels),\n      channelGroup("ARCHIVED", archivedChannels),\n    ].filter(Boolean);\n    $("channels").replaceChildren(...groups);\n    $("load-more-channels").classList.toggle("hidden", !state.channelDirectoryHasMore);\n    updateChannelHeader();\n    updateCapabilityUi();\n  }\n\n  function mergeChannelRows(existing, incoming) {\n    const byName = new Map(existing.map((channel) => [channel.channel, channel]));\n    for (const channel of incoming) byName.set(channel.channel, channel);\n    return Array.from(byName.values());\n  }\n\n  async function fetchChannelDirectoryPage(afterName = "") {\n    const cursor = afterName ? `&after_name=${encodeURIComponent(afterName)}` : "";\n    return api(`/api/channels?limit=${CHANNEL_DIRECTORY_PAGE_SIZE}${cursor}`);\n  }\n\n  function acceptChannelDirectoryPage(data, append) {\n    const rows = data.channels || [];\n    state.channels = append ? mergeChannelRows(state.channels, rows) : rows;\n    if (rows.length) state.channelDirectoryAfterName = rows[rows.length - 1].channel;\n    else if (!append) state.channelDirectoryAfterName = "";\n    state.channelDirectoryHasMore = Boolean(data.has_more);\n    renderChannels();\n  }\n\n  async function loadChannels() {\n    try {\n      const data = await fetchChannelDirectoryPage();\n      acceptChannelDirectoryPage(data, false);\n\n      const selectedChannelExists = state.channels.some((channel) => channel.channel === state.channel);\n      if (!selectedChannelExists) {\n        const preferred = state.channels.find((channel) => channel.status === "active") || state.channels[0];\n        state.channel = preferred ? preferred.channel : "";\n      }\n      if (state.channel) {\n        await selectChannel(state.channel);\n      } else {\n        $("channel-name").textContent = "No channels available";\n        resetHistory();\n      }\n    } catch (error) {\n      if (error.status === 401) disconnect("Session is no longer valid.");\n      else throw error;\n    }\n  }\n\n  async function loadMoreChannels() {\n    if (!state.channelDirectoryHasMore || !state.channelDirectoryAfterName) return;\n    const button = $("load-more-channels");\n    button.disabled = true;\n    try {\n      const data = await fetchChannelDirectoryPage(state.channelDirectoryAfterName);\n      acceptChannelDirectoryPage(data, true);\n    } catch (error) {\n      if (error.status === 401) disconnect("Session is no longer valid.");\n      else boardStatus(`Could not load more channels: ${error.message}`);\n    } finally {\n      button.disabled = false;\n    }\n  }\n'''
if old not in text:
    raise RuntimeError('channel rendering/loading anchor not found')
text = text.replace(old, new, 1)

old = '''  async function selectChannel(channel, refreshChannels = true) {\n    state.channel = channel;\n    state.followLatest = state.order === "desc";\n    state.historyTargetId = null;\n    state.replyTo = null;\n    $("channel-name").textContent = channel;\n    resetHistory();\n    updateReplyBar();\n\n    if (refreshChannels) {\n      const data = await api("/api/channels");\n      renderChannels(data.channels || []);\n    } else {\n      renderChannels(state.channels);\n    }\n\n    await loadLatestHistory();\n  }\n'''
new = '''  async function selectChannel(channel) {\n    state.channel = channel;\n    state.followLatest = state.order === "desc";\n    state.historyTargetId = null;\n    state.replyTo = null;\n    $("channel-name").textContent = channel;\n    resetHistory();\n    updateReplyBar();\n    renderChannels();\n    await loadLatestHistory();\n  }\n'''
if old not in text:
    raise RuntimeError('selectChannel anchor not found')
text = text.replace(old, new, 1)

old = '''  async function poll() {\n    if (!state.sessionToken || !state.channel || !state.followLatest || state.order !== "desc") return;\n    try {\n      const data = await api(`/api/messages?channel=${encodeURIComponent(state.channel)}&after=${state.lastId}&limit=200`);\n      for (const message of data.messages) appendMessage(message, "prepend");\n      if (data.messages.length) {\n        updateEmpty();\n        $("timeline").scrollTop = 0;\n        await refreshChannelCounts();\n      }\n    } catch (error) {\n      if (error.status === 401) disconnect("Session is no longer valid.");\n    }\n  }\n\n  function startPolling() {\n    if (state.pollTimer) clearInterval(state.pollTimer);\n    state.pollTimer = setInterval(poll, 4000);\n  }\n\n  async function refreshChannelCounts() {\n    const data = await api("/api/channels");\n    renderChannels(data.channels || []);\n  }\n'''
new = '''  function applyCurrentChannelActivity(messages, insertedCount) {\n    if (!insertedCount || !state.channel) return;\n    const summary = currentChannelSummary();\n    const latestId = messages.reduce((value, message) => Math.max(value, message.id || 0), 0);\n    const latestCreatedAt = messages.reduce((value, message) => Math.max(value, message.created_at || 0), 0);\n    if (summary) {\n      summary.message_count += insertedCount;\n      summary.last_id = Math.max(summary.last_id || 0, latestId);\n      summary.updated_at = Math.max(summary.updated_at || 0, latestCreatedAt);\n    } else {\n      state.channels.push({\n        channel: state.channel,\n        visibility: "private",\n        status: "active",\n        message_count: insertedCount,\n        last_id: latestId,\n        updated_at: latestCreatedAt,\n      });\n    }\n    renderChannels();\n  }\n\n  async function poll() {\n    if (!state.sessionToken || !state.channel || !state.followLatest || state.order !== "desc") return;\n    try {\n      const data = await api(`/api/messages?channel=${encodeURIComponent(state.channel)}&after=${state.lastId}&limit=200`);\n      let insertedCount = 0;\n      for (const message of data.messages) {\n        if (appendMessage(message, "prepend")) insertedCount += 1;\n      }\n      if (insertedCount) {\n        updateEmpty();\n        $("timeline").scrollTop = 0;\n        applyCurrentChannelActivity(data.messages, insertedCount);\n      }\n    } catch (error) {\n      if (error.status === 401) disconnect("Session is no longer valid.");\n    }\n  }\n\n  function startPolling() {\n    if (state.pollTimer) clearInterval(state.pollTimer);\n    state.pollTimer = setInterval(poll, 4000);\n  }\n'''
if old not in text:
    raise RuntimeError('poll/refresh anchor not found')
text = text.replace(old, new, 1)

old = '''      state.replyTo = null;\n      updateReplyBar();\n      await refreshChannelCounts();\n      await loadLatestHistory();\n'''
new = '''      state.replyTo = null;\n      updateReplyBar();\n      applyCurrentChannelActivity([data.message], 1);\n      await loadLatestHistory();\n'''
if old not in text:
    raise RuntimeError('post refresh anchor not found')
text = text.replace(old, new, 1)

old = '''  async function loadAdminChannels() {\n    $("admin-status").textContent = "";\n    try {\n      const data = await api("/api/admin/channels");\n      renderAdminChannels(data.channels || []);\n    } catch (error) {\n      if (error.status === 401) disconnect("Session is no longer valid.");\n      else $("admin-status").textContent = `Could not load channels: ${error.message}`;\n    }\n  }\n\n  function renderAdminChannels(channels) {\n'''
new = '''  async function fetchAdminChannelPage(afterName = "") {\n    const cursor = afterName ? `&after_name=${encodeURIComponent(afterName)}` : "";\n    return api(`/api/admin/channels?limit=${CHANNEL_DIRECTORY_PAGE_SIZE}${cursor}`);\n  }\n\n  function acceptAdminChannelPage(data, append) {\n    const rows = data.channels || [];\n    state.adminChannels = append ? mergeChannelRows(state.adminChannels, rows) : rows;\n    if (rows.length) state.adminChannelAfterName = rows[rows.length - 1].channel;\n    else if (!append) state.adminChannelAfterName = "";\n    state.adminChannelHasMore = Boolean(data.has_more);\n    renderAdminChannels(state.adminChannels);\n    $("load-more-admin-channels").classList.toggle("hidden", !state.adminChannelHasMore);\n  }\n\n  async function loadAdminChannels() {\n    $("admin-status").textContent = "";\n    try {\n      const data = await fetchAdminChannelPage();\n      acceptAdminChannelPage(data, false);\n    } catch (error) {\n      if (error.status === 401) disconnect("Session is no longer valid.");\n      else $("admin-status").textContent = `Could not load channels: ${error.message}`;\n    }\n  }\n\n  async function loadMoreAdminChannels() {\n    if (!state.adminChannelHasMore || !state.adminChannelAfterName) return;\n    const button = $("load-more-admin-channels");\n    button.disabled = true;\n    try {\n      const data = await fetchAdminChannelPage(state.adminChannelAfterName);\n      acceptAdminChannelPage(data, true);\n    } catch (error) {\n      if (error.status === 401) disconnect("Session is no longer valid.");\n      else $("admin-status").textContent = `Could not load more channels: ${error.message}`;\n    } finally {\n      button.disabled = false;\n    }\n  }\n\n  function renderAdminChannels(channels) {\n'''
if old not in text:
    raise RuntimeError('admin channel load anchor not found')
text = text.replace(old, new, 1)

text = text.replace(
'''      await Promise.all([loadAdminChannels(), refreshChannelCounts()]);\n''',
'''      await Promise.all([loadAdminChannels(), loadChannels()]);\n''',
)

anchor = '''  $("new-channel").addEventListener("click", createEphemeralChannel);\n'''
replacement = '''  $("new-channel").addEventListener("click", createEphemeralChannel);\n  $("load-more-channels").addEventListener("click", loadMoreChannels);\n'''
if anchor not in text:
    raise RuntimeError('main channel event anchor not found')
text = text.replace(anchor, replacement, 1)

anchor = '''  $("control-panel-back").addEventListener("click", closeControlPanel);\n'''
replacement = '''  $("control-panel-back").addEventListener("click", closeControlPanel);\n  $("load-more-admin-channels").addEventListener("click", loadMoreAdminChannels);\n'''
if anchor not in text:
    raise RuntimeError('admin channel event anchor not found')
text = text.replace(anchor, replacement, 1)

if 'refreshChannelCounts' in text:
    raise RuntimeError('unbounded browser directory refresh remains')

app.write_text(text)

html = Path('web/index.html')
h = html.read_text()
h = h.replace(
'''        <div id="channels" class="channels" aria-label="Channels"></div>\n''',
'''        <div id="channels" class="channels" aria-label="Channels"></div>\n        <button id="load-more-channels" class="secondary hidden" type="button">Load more channels</button>\n''',
1,
)
h = h.replace(
'''      </div>\n      <p id="admin-status" class="hint" aria-live="polite"></p>\n''',
'''      </div>\n      <button id="load-more-admin-channels" class="secondary hidden" type="button">Load more channels</button>\n      <p id="admin-status" class="hint" aria-live="polite"></p>\n''',
1,
)
html.write_text(h)

contract = Path('tests/browser_navigation_contract.rs')
c = contract.read_text()
c += r'''

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
'''
contract.write_text(c)
