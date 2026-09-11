(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    token: "",
    identity: null,
    channel: localStorage.getItem("conversation-blackboard.channel") || "",
    lastId: 0,
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

  async function loadChannels() {
    const data = await api("/api/channels");
    const container = $("channels");
    container.replaceChildren();
    for (const channel of data.channels) container.append(channelButton(channel));

    if (!state.channel && data.channels.length) state.channel = data.channels[0].channel;
    if (state.channel) await selectChannel(state.channel, false);
  }

  async function selectChannel(channel, refreshChannels = true) {
    state.channel = channel;
    state.lastId = 0;
    state.replyTo = null;
    localStorage.setItem("conversation-blackboard.channel", channel);
    $("channel-name").textContent = channel;
    $("timeline").replaceChildren();
    updateReplyBar();
    updateEmpty();

    if (refreshChannels) {
      const data = await api("/api/channels");
      const container = $("channels");
      container.replaceChildren(...data.channels.map(channelButton));
    }
    await loadInitialHistory();
  }

  async function loadInitialHistory() {
    let after = 0;
    while (true) {
      const data = await api(`/api/messages?channel=${encodeURIComponent(state.channel)}&after=${after}&limit=200`);
      for (const message of data.messages) appendMessage(message);
      if (data.messages.length < 200) break;
      after = data.messages[data.messages.length - 1].id;
    }
    updateEmpty();
    scrollToBottom();
  }

  async function poll() {
    if (!state.token || !state.channel) return;
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
    const container = $("channels");
    container.replaceChildren(...data.channels.map(channelButton));
  }

  function appendMessage(message) {
    if (document.querySelector(`[data-message-id="${message.id}"]`)) return;
    state.lastId = Math.max(state.lastId, message.id);

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
    $("timeline").append(article);

    requestAnimationFrame(() => {
      expandButton.hidden = body.scrollHeight <= body.clientHeight + 1;
    });
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
    const hasMessages = $("timeline").children.length > 0;
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
      appendMessage(data.message);
      $("body").value = "";
      state.replyTo = null;
      updateReplyBar();
      updateEmpty();
      scrollToBottom();
      await refreshChannelCounts();
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
    localStorage.setItem("conversation-blackboard.channel", channel);
    $("channel-name").textContent = channel;
    $("timeline").replaceChildren();
    state.lastId = 0;
    updateEmpty();
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
  $("refresh").addEventListener("click", async () => {
    if (!state.channel) return;
    await selectChannel(state.channel);
  });

  setConnected(false);
})();
