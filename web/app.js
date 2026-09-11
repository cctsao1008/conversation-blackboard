(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const HISTORY_PAGE_SIZE = 20;

  const state = {
    token: "",
    identity: null,
    channel: localStorage.getItem("conversation-blackboard.channel") || "",
    channels: [],
    lastId: 0,
    oldestId: 0,
    hasOlder: false,
    followLatest: true,
    replyTo: null,
    pollTimer: null,
  };

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(path, { ...options, headers });
    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      // Keep a predictable error shape if the server response is not JSON.
    }
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function setConnected(connected) {
    $("connect-panel").classList.toggle("hidden", connected);
    $("board").classList.toggle("hidden", !connected);
  }

  function identityText(identity) {
    const label = identity.label ? ` · ${identity.label}` : "";
    return `${identity.source} · ${identity.instance}${label}`;
  }

  async function connect() {
    $("connect-error").textContent = "";
    const token = $("token").value.trim();
    if (!token) {
      $("connect-error").textContent = "Enter a bearer token.";
      return;
    }

    state.token = token;
    try {
      state.identity = await api("/api/whoami");
      $("identity").textContent = identityText(state.identity);
      $("token").value = "";
      setConnected(true);
      await loadChannels();
      startPolling();
    } catch (error) {
      state.token = "";
      $("connect-error").textContent = error.status === 401 ? "Token was not accepted." : "Could not connect.";
    }
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

  function currentChannelSummary() {
    return state.channels.find((channel) => channel.channel === state.channel) || null;
  }

  function updateChannelHeader() {
    const summary = currentChannelSummary();
    $("channel-message-count").textContent = state.channel
      ? `#${summary ? summary.message_count : 0} messages`
      : "";
  }

  function renderChannels(channels) {
    state.channels = channels;
    const container = $("channels");
    container.replaceChildren(...channels.map(channelButton));
    updateChannelHeader();
  }

  async function loadChannels() {
    const data = await api("/api/channels");
    renderChannels(data.channels);

    const savedChannelExists = state.channels.some((channel) => channel.channel === state.channel);
    if (!savedChannelExists) {
      state.channel = state.channels.length ? state.channels[0].channel : "";
    }
    if (state.channel) await selectChannel(state.channel, false);
  }

  function resetHistory() {
    state.lastId = 0;
    state.oldestId = 0;
    state.hasOlder = false;
    $("timeline").replaceChildren();
    updateHistoryNav();
    updateEmpty();
  }

  async function selectChannel(channel, refreshChannels = true) {
    state.channel = channel;
    state.followLatest = true;
    state.replyTo = null;
    localStorage.setItem("conversation-blackboard.channel", channel);
    $("channel-name").textContent = channel;
    resetHistory();
    updateReplyBar();

    if (refreshChannels) {
      const data = await api("/api/channels");
      renderChannels(data.channels);
    } else {
      renderChannels(state.channels);
    }

    await loadLatestHistory();
  }

  async function loadLatestHistory() {
    if (!state.channel) return;

    state.followLatest = true;
    resetHistory();
    const data = await api(
      `/api/messages/window?channel=${encodeURIComponent(state.channel)}&limit=${HISTORY_PAGE_SIZE}`,
    );
    for (const message of data.messages) appendMessage(message);
    state.hasOlder = Boolean(data.has_older);
    updateHistoryNav();
    updateEmpty();
    scrollToBottom();
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
      if (error.status === 401) {
        disconnect("Session token is no longer valid.");
      } else {
        $("status").textContent = `Could not load older messages: ${error.message}`;
        updateHistoryNav();
      }
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

    const raw = $("jump-id").value.trim();
    const id = Number(raw);
    if (!Number.isSafeInteger(id) || id <= 0 || id >= Number.MAX_SAFE_INTEGER) {
      $("status").textContent = "Enter a valid positive message ID.";
      return;
    }

    if (focusMessage(id)) {
      $("status").textContent = `Showing #${id}`;
      return;
    }

    $("jump-go").disabled = true;
    $("status").textContent = `Finding #${id}…`;
    try {
      const data = await api(
        `/api/messages/window?channel=${encodeURIComponent(state.channel)}&before=${id + 1}&limit=${HISTORY_PAGE_SIZE}`,
      );
      const found = data.messages.some((message) => message.id === id);
      if (!found) {
        $("status").textContent = `Message #${id} is not in ${state.channel}.`;
        return;
      }

      state.followLatest = false;
      resetHistory();
      for (const message of data.messages) appendMessage(message);
      state.hasOlder = Boolean(data.has_older);
      updateHistoryNav();
      updateEmpty();

      requestAnimationFrame(() => focusMessage(id));
      $("status").textContent = `Showing #${id}. Select Latest to return to the live window.`;
    } catch (error) {
      if (error.status === 401) {
        disconnect("Session token is no longer valid.");
      } else {
        $("status").textContent = `Could not find #${id}: ${error.message}`;
      }
    } finally {
      $("jump-go").disabled = false;
    }
  }

  async function poll() {
    if (!state.token || !state.channel || !state.followLatest) return;
    try {
      const data = await api(`/api/messages?channel=${encodeURIComponent(state.channel)}&after=${state.lastId}&limit=200`);
      for (const message of data.messages) appendMessage(message);
      if (data.messages.length) {
        updateEmpty();
        scrollToBottom();
        await refreshChannelCounts();
      }
    } catch (error) {
      if (error.status === 401) disconnect("Session token is no longer valid.");
    }
  }

  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(poll, 4000);
  }

  async function refreshChannelCounts() {
    const data = await api("/api/channels");
    renderChannels(data.channels);
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

    const replyButton = document.createElement("button");
    replyButton.type = "button";
    replyButton.className = "secondary";
    replyButton.textContent = "Reply";
    replyButton.addEventListener("click", () => {
      state.replyTo = { id: message.id, source: message.source, instance: message.instance };
      updateReplyBar();
      $("body").focus();
    });

    actions.append(expandButton, replyButton);
    article.append(meta, body, actions);

    const timeline = $("timeline");
    if (placement === "prepend") {
      timeline.insertBefore(article, timeline.firstChild);
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
    const hasMessages = $("timeline").querySelector(".message") !== null;
    $("empty").classList.toggle("visible", !hasMessages);
  }

  function scrollToBottom() {
    const timeline = $("timeline");
    timeline.scrollTop = timeline.scrollHeight;
  }

  async function postMessage(event) {
    event.preventDefault();
    if (!state.channel) {
      await createChannel();
      if (!state.channel) return;
    }

    const body = $("body").value.trim();
    if (!body) return;

    $("send").disabled = true;
    $("status").textContent = "Posting…";
    try {
      const payload = {
        channel: state.channel,
        kind: $("kind").value,
        body,
        reply_to: state.replyTo ? state.replyTo.id : null,
      };
      const data = await api("/api/messages", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("body").value = "";
      state.replyTo = null;
      updateReplyBar();
      await refreshChannelCounts();
      await loadLatestHistory();
      $("status").textContent = `Posted #${data.message.id}`;
    } catch (error) {
      $("status").textContent = error.status === 401 ? "Token is no longer valid." : `Post failed: ${error.message}`;
    } finally {
      $("send").disabled = false;
    }
  }

  async function createChannel() {
    const proposed = window.prompt("Channel name (letters, digits, . _ : / -)", "");
    if (!proposed) return;
    const channel = proposed.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(channel)) {
      window.alert("Invalid channel name.");
      return;
    }
    state.channel = channel;
    state.followLatest = true;
    localStorage.setItem("conversation-blackboard.channel", channel);
    $("channel-name").textContent = channel;
    resetHistory();
    updateChannelHeader();
  }

  function disconnect(message) {
    state.token = "";
    state.identity = null;
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = null;
    $("identity").textContent = "Not connected";
    setConnected(false);
    $("connect-error").textContent = message || "";
  }

  $("connect").addEventListener("click", connect);
  $("token").addEventListener("keydown", (event) => {
    if (event.key === "Enter") connect();
  });
  $("composer").addEventListener("submit", postMessage);
  $("cancel-reply").addEventListener("click", () => {
    state.replyTo = null;
    updateReplyBar();
  });
  $("new-channel").addEventListener("click", createChannel);
  $("latest").addEventListener("click", loadLatestHistory);
  $("refresh").addEventListener("click", async () => {
    if (!state.channel) return;
    await refreshChannelCounts();
    await loadLatestHistory();
  });
  $("jump-form").addEventListener("submit", jumpToMessage);

  setConnected(false);
})();
