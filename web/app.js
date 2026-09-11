(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const HISTORY_PAGE_SIZE = 20;
  const THEME_KEY = "conversation-blackboard-theme";
  const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

  const state = {
    participantId: "",
    sessionToken: "",
    identity: null,
    role: "",
    sessionType: "",
    channel: "",
    channels: [],
    lastId: 0,
    oldestId: 0,
    hasOlder: false,
    followLatest: true,
    replyTo: null,
    pollTimer: null,
    editChannel: "",
    theme: "system",
  };

  async function api(path, options = {}) {
    const { skipSession = false, ...fetchOptions } = options;
    const headers = new Headers(fetchOptions.headers || {});
    headers.set("Accept", "application/json");
    if (!skipSession && state.sessionToken) {
      headers.set("X-Blackboard-Web-Session", state.sessionToken);
    }
    if (fetchOptions.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(path, { ...fetchOptions, headers });
    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      // Preserve a predictable error shape for non-JSON server failures.
    }
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function storageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_) {
      // Theme persistence is optional; authentication never depends on storage.
    }
  }

  function applyTheme(theme, persist = true) {
    if (!["system", "light", "dracula"].includes(theme)) theme = "system";
    state.theme = theme;
    $("theme").value = theme;
    if (theme === "system") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.dataset.theme = theme;
    }
    if (persist) storageSet(THEME_KEY, theme);
  }

  function isGuest() {
    return state.sessionType === "guest";
  }

  function isAdmin() {
    return state.sessionType === "human-web" && state.role === "admin";
  }

  function currentChannelSummary() {
    return state.channels.find((channel) => channel.channel === state.channel) || null;
  }

  function canWriteCurrentChannel() {
    const summary = currentChannelSummary();
    return !isGuest() && (!summary || summary.status === "active");
  }

  function identityText() {
    if (!state.identity) return "Not connected";
    if (isGuest()) return "anonymous · read only";
    if (isAdmin()) return `${state.identity.instance} · admin`;
    return state.identity.instance;
  }

  function setConnected(connected) {
    $("connect-panel").classList.toggle("hidden", connected);
    $("board").classList.toggle("hidden", !connected);
    $("control-panel").classList.add("hidden");
    $("identity").textContent = connected ? identityText() : "Not connected";
    $("disconnect").classList.toggle("hidden", !connected);
    $("control-panel-open").classList.toggle("hidden", !connected || !isAdmin());
  }

  function updateCapabilityUi() {
    const guest = isGuest();
    const writable = canWriteCurrentChannel();
    $("new-channel").classList.toggle("hidden", guest);
    $("composer").classList.toggle("hidden", !writable);
    $("guest-readonly").classList.toggle("hidden", writable);
    $("control-panel-open").classList.toggle("hidden", !isAdmin());

    if (!writable) {
      const notice = $("guest-readonly");
      const strong = notice.querySelector("strong");
      const span = notice.querySelector("span");
      if (guest) {
        strong.textContent = "Guest mode · Read only";
        span.textContent = "Sign in as a participant to post messages.";
      } else {
        strong.textContent = "Archived channel · Read only";
        span.textContent = "Reactivate this channel in Control Panel to post messages.";
      }
    }
  }

  async function finishLogin(auth) {
    state.participantId = auth.instance;
    state.sessionToken = auth.session_token;
    state.identity = auth;
    state.role = auth.role || "user";
    state.sessionType = auth.session_type || "human-web";
    state.channel = "";
    $("totp-code").value = "";
    setConnected(true);
    updateCapabilityUi();
    await loadChannels();
    startPolling();
  }

  async function connect() {
    $("connect-error").textContent = "";
    const participantId = $("participant-id").value.trim();
    const code = $("totp-code").value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/.test(participantId)) {
      $("connect-error").textContent = "Enter a valid participant ID.";
      return;
    }
    if (!/^\d{6}$/.test(code)) {
      $("connect-error").textContent = "Enter the current 6-digit authenticator code.";
      return;
    }

    const button = $("connect");
    button.disabled = true;
    button.textContent = "Connecting…";
    try {
      const auth = await api("/api/auth/totp", {
        method: "POST",
        body: JSON.stringify({ participant_id: participantId, code }),
        skipSession: true,
      });
      await finishLogin(auth);
    } catch (error) {
      clearSession();
      setConnected(false);
      $("connect-error").textContent = error.status === 401
        ? "Participant or authenticator code was not accepted."
        : (error.message || "Could not connect.");
    } finally {
      button.disabled = false;
      button.textContent = "Connect";
    }
  }

  async function connectGuest() {
    $("connect-error").textContent = "";
    const button = $("guest-connect");
    button.disabled = true;
    button.textContent = "Opening…";
    try {
      const auth = await api("/api/auth/guest", {
        method: "POST",
        skipSession: true,
      });
      await finishLogin(auth);
    } catch (error) {
      clearSession();
      setConnected(false);
      $("connect-error").textContent = error.message || "Guest access is unavailable.";
    } finally {
      button.disabled = false;
      button.textContent = "Continue as Guest";
    }
  }

  function clearSession() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = null;
    state.participantId = "";
    state.sessionToken = "";
    state.identity = null;
    state.role = "";
    state.sessionType = "";
    state.channel = "";
    state.channels = [];
    state.replyTo = null;
    resetHistory();
  }

  function disconnect(message = "") {
    clearSession();
    setConnected(false);
    $("channels").replaceChildren();
    $("channel-name").textContent = "Select a channel";
    $("channel-message-count").textContent = "";
    $("connect-error").textContent = message;
  }

  function channelButton(channel) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "channel-button";
    if (channel.channel === state.channel) button.classList.add("active");

    const name = document.createElement("span");
    name.className = "channel-name-text";
    name.textContent = channel.channel;
    name.title = channel.channel;

    const count = document.createElement("span");
    count.className = "channel-count";
    count.textContent = String(channel.message_count);

    button.append(name, count);
    button.addEventListener("click", () => selectChannel(channel.channel));
    return button;
  }

  function channelGroup(label, channels) {
    if (!channels.length) return null;
    const section = document.createElement("section");
    section.className = "channel-group";
    const heading = document.createElement("div");
    heading.className = "channel-group-title";
    heading.textContent = label;
    const list = document.createElement("div");
    list.className = "channel-group-list";
    list.append(...channels.map(channelButton));
    section.append(heading, list);
    return section;
  }

  function updateChannelHeader() {
    const summary = currentChannelSummary();
    $("channel-message-count").textContent = state.channel
      ? `#${summary ? summary.message_count : 0} messages`
      : "";
  }

  function renderChannels(channels) {
    state.channels = channels;
    const publicChannels = channels.filter((channel) => channel.status === "active" && channel.visibility === "public");
    const privateChannels = channels.filter((channel) => channel.status === "active" && channel.visibility === "private");
    const archivedChannels = channels.filter((channel) => channel.status === "archived");
    const groups = [
      channelGroup("PUBLIC", publicChannels),
      channelGroup("PRIVATE", privateChannels),
      channelGroup("ARCHIVED", archivedChannels),
    ].filter(Boolean);
    $("channels").replaceChildren(...groups);
    updateChannelHeader();
    updateCapabilityUi();
  }

  async function loadChannels() {
    try {
      const data = await api("/api/channels");
      renderChannels(data.channels || []);

      const selectedChannelExists = state.channels.some((channel) => channel.channel === state.channel);
      if (!selectedChannelExists) {
        const preferred = state.channels.find((channel) => channel.status === "active") || state.channels[0];
        state.channel = preferred ? preferred.channel : "";
      }
      if (state.channel) {
        await selectChannel(state.channel, false);
      } else {
        $("channel-name").textContent = "No channels available";
        resetHistory();
      }
    } catch (error) {
      if (error.status === 401) disconnect("Session is no longer valid.");
      else throw error;
    }
  }

  function emptyElement() {
    const empty = document.createElement("div");
    empty.id = "empty";
    empty.className = "empty visible";
    empty.textContent = "No messages in this channel yet.";
    return empty;
  }

  function resetHistory() {
    state.lastId = 0;
    state.oldestId = 0;
    state.hasOlder = false;
    const timeline = $("timeline");
    if (timeline) timeline.replaceChildren(emptyElement());
  }

  async function selectChannel(channel, refreshChannels = true) {
    state.channel = channel;
    state.followLatest = true;
    state.replyTo = null;
    $("channel-name").textContent = channel;
    resetHistory();
    updateReplyBar();

    if (refreshChannels) {
      const data = await api("/api/channels");
      renderChannels(data.channels || []);
    } else {
      renderChannels(state.channels);
    }

    await loadLatestHistory();
  }

  async function loadLatestHistory() {
    if (!state.channel) return;

    state.followLatest = true;
    resetHistory();
    try {
      const data = await api(
        `/api/messages/window?channel=${encodeURIComponent(state.channel)}&limit=${HISTORY_PAGE_SIZE}`,
      );
      for (const message of data.messages) appendMessage(message);
      state.hasOlder = Boolean(data.has_older);
      updateHistoryNav();
      updateEmpty();
      scrollToBottom();
    } catch (error) {
      if (error.status === 401) disconnect("Session is no longer valid.");
      else boardStatus(`Could not load channel: ${error.message}`);
    }
  }

  async function loadOlderHistory() {
    if (!state.channel || !state.oldestId || !state.hasOlder) return;

    const button = $("load-older");
    if (button) {
      button.disabled = true;
      button.textContent = "Loading…";
    }

    try {
      const data = await api(
        `/api/messages/window?channel=${encodeURIComponent(state.channel)}&before=${state.oldestId}&limit=${HISTORY_PAGE_SIZE}`,
      );
      for (let index = data.messages.length - 1; index >= 0; index -= 1) {
        appendMessage(data.messages[index], "prepend");
      }
      state.hasOlder = Boolean(data.has_older);
      updateHistoryNav();
      updateEmpty();
      if (data.messages.length) $("timeline").scrollTop = 0;
    } catch (error) {
      if (error.status === 401) disconnect("Session is no longer valid.");
      else boardStatus(`Could not load older messages: ${error.message}`);
    }
  }

  function focusMessage(id) {
    const article = document.querySelector(`[data-message-id="${id}"]`);
    if (!article) return false;
    article.classList.add("message-target");
    article.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => article.classList.remove("message-target"), 1800);
    return true;
  }

  async function jumpToMessage(event) {
    event.preventDefault();
    if (!state.channel) return;

    const id = Number($("jump-id").value.trim());
    if (!Number.isSafeInteger(id) || id <= 0 || id >= Number.MAX_SAFE_INTEGER) {
      boardStatus("Enter a valid positive message ID.");
      return;
    }
    if (focusMessage(id)) {
      boardStatus(`Showing #${id}`);
      return;
    }

    $("jump-go").disabled = true;
    boardStatus(`Finding #${id}…`);
    try {
      const data = await api(
        `/api/messages/window?channel=${encodeURIComponent(state.channel)}&before=${id + 1}&limit=${HISTORY_PAGE_SIZE}`,
      );
      const found = data.messages.some((message) => message.id === id);
      if (!found) {
        boardStatus(`Message #${id} is not in ${state.channel}.`);
        return;
      }
      state.followLatest = false;
      resetHistory();
      for (const message of data.messages) appendMessage(message);
      state.hasOlder = Boolean(data.has_older);
      updateHistoryNav();
      updateEmpty();
      requestAnimationFrame(() => focusMessage(id));
      boardStatus(`Showing #${id}. Select Latest to return to the live window.`);
    } catch (error) {
      if (error.status === 401) disconnect("Session is no longer valid.");
      else boardStatus(`Could not find #${id}: ${error.message}`);
    } finally {
      $("jump-go").disabled = false;
    }
  }

  async function poll() {
    if (!state.sessionToken || !state.channel || !state.followLatest) return;
    try {
      const data = await api(`/api/messages?channel=${encodeURIComponent(state.channel)}&after=${state.lastId}&limit=200`);
      for (const message of data.messages) appendMessage(message);
      if (data.messages.length) {
        updateEmpty();
        scrollToBottom();
        await refreshChannelCounts();
      }
    } catch (error) {
      if (error.status === 401) disconnect("Session is no longer valid.");
    }
  }

  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(poll, 4000);
  }

  async function refreshChannelCounts() {
    const data = await api("/api/channels");
    renderChannels(data.channels || []);
  }

  function appendMessage(message, placement = "append") {
    if (document.querySelector(`[data-message-id="${message.id}"]`)) return false;

    state.lastId = Math.max(state.lastId, message.id);
    state.oldestId = state.oldestId ? Math.min(state.oldestId, message.id) : message.id;

    const article = document.createElement("article");
    article.className = "message";
    article.dataset.messageId = String(message.id);

    const meta = document.createElement("div");
    meta.className = "message-meta";
    const source = document.createElement("span");
    source.className = "message-source";
    source.textContent = message.source;
    const instance = document.createElement("span");
    instance.textContent = message.instance;
    const kind = document.createElement("span");
    kind.className = "message-kind";
    kind.textContent = message.kind;
    const time = document.createElement("time");
    time.dateTime = new Date(message.created_at * 1000).toISOString();
    time.textContent = new Date(message.created_at * 1000).toLocaleString();
    const id = document.createElement("span");
    id.textContent = `#${message.id}`;
    meta.append(source, instance, kind, time, id);
    if (message.reply_to) {
      const reply = document.createElement("span");
      reply.textContent = `↩ #${message.reply_to}`;
      meta.append(reply);
    }

    const body = document.createElement("p");
    body.className = "message-body collapsed";
    body.textContent = message.body;

    const actions = document.createElement("div");
    actions.className = "message-actions";
    const expandButton = document.createElement("button");
    expandButton.type = "button";
    expandButton.className = "secondary message-expand";
    expandButton.textContent = "Expand";
    expandButton.setAttribute("aria-expanded", "false");
    expandButton.hidden = true;
    expandButton.addEventListener("click", () => {
      const expanded = body.classList.toggle("expanded");
      body.classList.toggle("collapsed", !expanded);
      expandButton.textContent = expanded ? "Collapse" : "Expand";
      expandButton.setAttribute("aria-expanded", String(expanded));
    });
    actions.append(expandButton);

    if (canWriteCurrentChannel()) {
      const replyButton = document.createElement("button");
      replyButton.type = "button";
      replyButton.className = "secondary";
      replyButton.textContent = "Reply";
      replyButton.addEventListener("click", () => {
        state.replyTo = { id: message.id, source: message.source, instance: message.instance };
        updateReplyBar();
        $("body").focus();
      });
      actions.append(replyButton);
    }

    article.append(meta, body, actions);
    const timeline = $("timeline");
    const empty = $("empty");
    if (placement === "prepend") {
      const firstMessage = timeline.querySelector(".message");
      timeline.insertBefore(article, firstMessage || empty?.nextSibling || null);
    } else {
      const historyNav = $("history-nav");
      timeline.insertBefore(article, historyNav || null);
    }

    requestAnimationFrame(() => {
      expandButton.hidden = body.scrollHeight <= body.clientHeight + 1;
    });
    return true;
  }

  function updateHistoryNav() {
    const existing = $("history-nav");
    if (existing) existing.remove();
    if (!state.hasOlder || !state.channel) return;

    const nav = document.createElement("div");
    nav.id = "history-nav";
    nav.className = "history-nav";
    const button = document.createElement("button");
    button.id = "load-older";
    button.type = "button";
    button.className = "secondary";
    button.textContent = "Load older messages";
    button.addEventListener("click", loadOlderHistory);
    nav.append(button);
    $("timeline").append(nav);
  }

  function updateReplyBar() {
    const bar = $("reply-bar");
    if (!state.replyTo) {
      bar.classList.add("hidden");
      $("reply-label").textContent = "";
      return;
    }
    bar.classList.remove("hidden");
    $("reply-label").textContent = `Replying to #${state.replyTo.id} · ${state.replyTo.source} · ${state.replyTo.instance}`;
  }

  function updateEmpty() {
    const empty = $("empty");
    if (!empty) return;
    const hasMessages = $("timeline").querySelector(".message") !== null;
    empty.classList.toggle("visible", !hasMessages);
  }

  function scrollToBottom() {
    const timeline = $("timeline");
    timeline.scrollTop = timeline.scrollHeight;
  }

  function boardStatus(text) {
    if (canWriteCurrentChannel()) {
      $("status").textContent = text;
    } else {
      const notice = $("guest-readonly").querySelector("span");
      if (text) notice.textContent = text;
    }
  }

  async function postMessage(event) {
    event.preventDefault();
    if (!canWriteCurrentChannel()) return;
    if (!state.channel) {
      await createEphemeralChannel();
      if (!state.channel) return;
    }

    const body = $("body").value.trim();
    if (!body) return;

    $("send").disabled = true;
    $("status").textContent = "Posting…";
    try {
      const data = await api("/api/messages", {
        method: "POST",
        body: JSON.stringify({
          channel: state.channel,
          kind: $("kind").value,
          body,
          reply_to: state.replyTo ? state.replyTo.id : null,
        }),
      });
      $("body").value = "";
      state.replyTo = null;
      updateReplyBar();
      await refreshChannelCounts();
      await loadLatestHistory();
      $("status").textContent = `Posted #${data.message.id}`;
    } catch (error) {
      if (error.status === 401) disconnect("Session is no longer valid.");
      else $("status").textContent = `Post failed: ${error.message}`;
    } finally {
      $("send").disabled = false;
    }
  }

  async function createEphemeralChannel() {
    if (isGuest()) return;
    const name = window.prompt("Channel name");
    if (name === null) return;
    const channel = name.trim();
    if (!NAME_RE.test(channel)) {
      boardStatus("Channel names use letters, numbers, '.', '_', ':', '/', or '-'.");
      return;
    }
    state.channel = channel;
    state.replyTo = null;
    $("channel-name").textContent = channel;
    resetHistory();
    updateReplyBar();
    updateCapabilityUi();
    boardStatus("New channels become private when the first message is posted.");
  }

  async function openControlPanel() {
    if (!isAdmin()) return;
    $("board").classList.add("hidden");
    $("control-panel").classList.remove("hidden");
    await loadAdminChannels();
  }

  function closeControlPanel() {
    $("control-panel").classList.add("hidden");
    $("board").classList.remove("hidden");
  }

  async function loadAdminChannels() {
    $("admin-status").textContent = "";
    try {
      const data = await api("/api/admin/channels");
      renderAdminChannels(data.channels || []);
    } catch (error) {
      if (error.status === 401) disconnect("Session is no longer valid.");
      else $("admin-status").textContent = `Could not load channels: ${error.message}`;
    }
  }

  function renderAdminChannels(channels) {
    const rows = channels.map((channel) => {
      const tr = document.createElement("tr");
      const name = document.createElement("td");
      name.className = "admin-channel-name";
      name.textContent = channel.channel;
      const visibility = document.createElement("td");
      visibility.textContent = titleCase(channel.visibility);
      const status = document.createElement("td");
      status.textContent = titleCase(channel.status);
      const count = document.createElement("td");
      count.className = "numeric";
      count.textContent = String(channel.message_count);
      const updated = document.createElement("td");
      updated.textContent = formatTimestamp(channel.updated_at);
      const actions = document.createElement("td");
      actions.className = "table-actions";
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "secondary compact";
      edit.textContent = "Edit";
      edit.addEventListener("click", () => openEditChannel(channel));
      actions.append(edit);
      tr.append(name, visibility, status, count, updated, actions);
      if (channel.status === "archived") tr.classList.add("archived-row");
      return tr;
    });
    $("admin-channels").replaceChildren(...rows);
  }

  function openCreateChannel() {
    $("create-channel-name").value = "";
    $("create-channel-visibility").value = "private";
    $("create-channel-error").textContent = "";
    $("create-channel-dialog").showModal();
    $("create-channel-name").focus();
  }

  async function submitCreateChannel(event) {
    event.preventDefault();
    const name = $("create-channel-name").value.trim();
    const visibility = $("create-channel-visibility").value;
    if (!NAME_RE.test(name)) {
      $("create-channel-error").textContent = "Enter a valid channel name.";
      return;
    }
    try {
      await api("/api/admin/channels", {
        method: "POST",
        body: JSON.stringify({ name, visibility }),
      });
      $("create-channel-dialog").close();
      await Promise.all([loadAdminChannels(), refreshChannelCounts()]);
      $("admin-status").textContent = `Created ${name}.`;
    } catch (error) {
      $("create-channel-error").textContent = error.status === 409
        ? "That channel already exists."
        : error.message;
    }
  }

  function openEditChannel(channel) {
    state.editChannel = channel.channel;
    $("edit-channel-name").textContent = channel.channel;
    $("edit-channel-visibility").value = channel.visibility;
    $("edit-channel-status").value = channel.status;
    $("edit-channel-error").textContent = "";
    $("edit-channel-dialog").showModal();
  }

  async function submitEditChannel(event) {
    event.preventDefault();
    if (!state.editChannel) return;
    const visibility = $("edit-channel-visibility").value;
    const status = $("edit-channel-status").value;
    try {
      await api(`/api/admin/channels/${encodeURIComponent(state.editChannel)}`, {
        method: "PATCH",
        body: JSON.stringify({ visibility, status }),
      });
      const edited = state.editChannel;
      state.editChannel = "";
      $("edit-channel-dialog").close();
      await Promise.all([loadAdminChannels(), refreshChannelCounts()]);
      updateCapabilityUi();
      $("admin-status").textContent = `Updated ${edited}.`;
    } catch (error) {
      $("edit-channel-error").textContent = error.message;
    }
  }

  function titleCase(value) {
    return value ? `${value.charAt(0).toUpperCase()}${value.slice(1)}` : "";
  }

  function formatTimestamp(unixSeconds) {
    if (!unixSeconds) return "—";
    return new Date(unixSeconds * 1000).toLocaleString();
  }

  $("connect").addEventListener("click", connect);
  $("guest-connect").addEventListener("click", connectGuest);
  $("totp-code").addEventListener("keydown", (event) => {
    if (event.key === "Enter") connect();
  });
  $("participant-id").addEventListener("keydown", (event) => {
    if (event.key === "Enter") connect();
  });
  $("disconnect").addEventListener("click", () => disconnect());
  $("new-channel").addEventListener("click", createEphemeralChannel);
  $("refresh").addEventListener("click", async () => {
    await refreshChannelCounts();
    await loadLatestHistory();
  });
  $("latest").addEventListener("click", loadLatestHistory);
  $("jump-form").addEventListener("submit", jumpToMessage);
  $("composer").addEventListener("submit", postMessage);
  $("cancel-reply").addEventListener("click", () => {
    state.replyTo = null;
    updateReplyBar();
  });
  $("control-panel-open").addEventListener("click", openControlPanel);
  $("control-panel-back").addEventListener("click", closeControlPanel);
  $("create-channel").addEventListener("click", openCreateChannel);
  $("create-channel-form").addEventListener("submit", submitCreateChannel);
  $("create-channel-cancel").addEventListener("click", () => $("create-channel-dialog").close());
  $("edit-channel-form").addEventListener("submit", submitEditChannel);
  $("edit-channel-cancel").addEventListener("click", () => {
    state.editChannel = "";
    $("edit-channel-dialog").close();
  });
  $("theme").addEventListener("change", (event) => applyTheme(event.target.value));

  applyTheme(storageGet(THEME_KEY) || "system", false);
  setConnected(false);
  resetHistory();
})();
