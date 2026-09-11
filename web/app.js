(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const HISTORY_PAGE_SIZE = 20;

  const state = {
    participantId: "",
    signingKey: null,
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

  function base64urlDecode(value) {
    const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  }

  function base64urlEncode(value) {
    const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function canonicalJson(value) {
    if (value === null || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }

  function parseCredentialBundle(value) {
    const prefix = "bbcred-v1:";
    if (!value.startsWith(prefix)) throw new Error("Unsupported participant credential format.");
    const decoded = new TextDecoder().decode(base64urlDecode(value.slice(prefix.length)));
    const bundle = JSON.parse(decoded);
    if (
      bundle.version !== "bbcred-v1"
      || typeof bundle.participant_id !== "string"
      || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/.test(bundle.participant_id)
      || typeof bundle.private_key !== "string"
      || !bundle.private_key.startsWith("ed25519-sk:")
    ) {
      throw new Error("Invalid participant credential.");
    }
    return bundle;
  }

  function bytesStartWith(value, prefix, offset = 0) {
    if (value.length < offset + prefix.length) return false;
    for (let index = 0; index < prefix.length; index += 1) {
      if (value[offset + index] !== prefix[index]) return false;
    }
    return true;
  }

  function normalizeEd25519Pkcs8ForWebCrypto(pkcs8) {
    // ring::Ed25519KeyPair::generate_pkcs8() emits PKCS#8 v2 OneAsymmetricKey:
    //   30 51 02 01 01 ... 04 22 04 20 <32-byte seed> 81 21 00 <32-byte public key>
    // Some WebCrypto implementations accept only RFC 5208 PrivateKeyInfo v1:
    //   30 2e 02 01 00 ... 04 22 04 20 <32-byte seed>
    // The conversion is local serialization normalization only; key material is unchanged.
    const v1Prefix = Uint8Array.of(
      0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06,
      0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
    );
    const ringV2Prefix = Uint8Array.of(
      0x30, 0x51, 0x02, 0x01, 0x01, 0x30, 0x05, 0x06,
      0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
    );
    const ringV2PublicMarker = Uint8Array.of(0x81, 0x21, 0x00);

    if (pkcs8.length === 48 && bytesStartWith(pkcs8, v1Prefix)) return pkcs8;

    if (
      pkcs8.length === 83
      && bytesStartWith(pkcs8, ringV2Prefix)
      && bytesStartWith(pkcs8, ringV2PublicMarker, 48)
    ) {
      const normalized = new Uint8Array(48);
      normalized.set(v1Prefix, 0);
      normalized.set(pkcs8.slice(16, 48), 16);
      return normalized;
    }

    return pkcs8;
  }

  async function importSigningKey(privateKey) {
    const encoded = privateKey.slice("ed25519-sk:".length);
    const pkcs8 = base64urlDecode(encoded);

    try {
      return await crypto.subtle.importKey("pkcs8", pkcs8, { name: "Ed25519" }, false, ["sign"]);
    } catch (originalError) {
      const normalized = normalizeEd25519Pkcs8ForWebCrypto(pkcs8);
      if (normalized === pkcs8) throw originalError;
      try {
        return await crypto.subtle.importKey(
          "pkcs8",
          normalized,
          { name: "Ed25519" },
          false,
          ["sign"],
        );
      } catch (_) {
        throw new Error("Browser could not import this Ed25519 participant credential.");
      }
    }
  }

  async function signObject(signingKey, value) {
    const bytes = new TextEncoder().encode(canonicalJson(value));
    const signature = await crypto.subtle.sign({ name: "Ed25519" }, signingKey, bytes);
    return base64urlEncode(signature);
  }

  async function signedReadHeaders(method, requestTarget) {
    const signature = await signObject(state.signingKey, {
      method,
      participant_id: state.participantId,
      purpose: "http-request-auth-v1",
      request_target: requestTarget,
      signature_version: "ed25519-v1",
    });
    return {
      "X-Blackboard-Participant-Id": state.participantId,
      "X-Blackboard-Signature-Scheme": "ed25519-v1",
      "X-Blackboard-Signature": signature,
    };
  }

  async function signedWriteEnvelope(channel, kind, body, replyTo) {
    const nonce = `web-${crypto.randomUUID()}`;
    const payload = {
      signature_version: "ed25519-v1",
      participant_id: state.participantId,
      channel,
      kind,
      body,
      reply_to: replyTo,
      nonce,
    };
    const signature = await signObject(state.signingKey, payload);
    return {
      participant_id: state.participantId,
      channel,
      kind,
      body,
      reply_to: replyTo,
      nonce,
      auth: {
        scheme: "ed25519-v1",
        signature,
      },
    };
  }

  async function api(path, options = {}) {
    const { skipRequestAuth = false, ...fetchOptions } = options;
    const method = (fetchOptions.method || "GET").toUpperCase();
    const headers = new Headers(fetchOptions.headers || {});
    headers.set("Accept", "application/json");
    if (!skipRequestAuth && method === "GET" && state.participantId && state.signingKey) {
      const authHeaders = await signedReadHeaders(method, path);
      for (const [name, value] of Object.entries(authHeaders)) headers.set(name, value);
    }
    if (fetchOptions.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(path, { ...fetchOptions, headers });
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
    return identity.instance;
  }

  async function connect() {
    $("connect-error").textContent = "";
    const credential = $("participant-credential").value.trim();
    if (!credential) {
      $("connect-error").textContent = "Enter a participant credential.";
      return;
    }

    try {
      const bundle = parseCredentialBundle(credential);
      const signingKey = await importSigningKey(bundle.private_key);
      const challenge = await api("/api/auth/challenge", {
        method: "POST",
        body: JSON.stringify({ participant_id: bundle.participant_id }),
        skipRequestAuth: true,
      });
      const signature = await signObject(signingKey, {
        challenge: challenge.challenge,
        expires_at: challenge.expires_at,
        participant_id: bundle.participant_id,
        purpose: "browser-connect-v1",
        signature_version: "ed25519-v1",
      });
      const identity = await api("/api/auth/verify", {
        method: "POST",
        body: JSON.stringify({
          participant_id: bundle.participant_id,
          challenge: challenge.challenge,
          expires_at: challenge.expires_at,
          challenge_token: challenge.challenge_token,
          auth: {
            scheme: "ed25519-v1",
            signature,
          },
        }),
        skipRequestAuth: true,
      });

      state.participantId = bundle.participant_id;
      state.signingKey = signingKey;
      state.identity = identity;
      $("identity").textContent = identityText(state.identity);
      $("participant-credential").value = "";
      setConnected(true);
      await loadChannels();
      startPolling();
    } catch (error) {
      state.participantId = "";
      state.signingKey = null;
      state.identity = null;
      $("connect-error").textContent = error.status === 401
        ? "Participant credential was not accepted."
        : (error.message || "Could not connect.");
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
        disconnect("Participant credential is no longer valid.");
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
        disconnect("Participant credential is no longer valid.");
      } else {
        $("status").textContent = `Could not find #${id}: ${error.message}`;
      }
    } finally {
      $("jump-go").disabled = false;
    }
  }

  async function poll() {
    if (!state.signingKey || !state.channel || !state.followLatest) return;
    try {
      const data = await api(`/api/messages?channel=${encodeURIComponent(state.channel)}&after=${state.lastId}&limit=200`);
      for (const message of data.messages) appendMessage(message);
      if (data.messages.length) {
        updateEmpty();
        scrollToBottom();
        await refreshChannelCounts();
      }
    } catch (error) {
      if (error.status === 401) disconnect("Participant credential is no longer valid.");
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
      const payload = await signedWriteEnvelope(
        state.channel,
        $("kind").value,
        body,
        state.replyTo ? state.replyTo.id : null,
      );
      const data = await api("/api/messages", {
        method: "POST",
        body: JSON.stringify(payload),
        skipRequestAuth: true,
      });
      $("body").value = "";
      state.replyTo = null;
      updateReplyBar();
      await refreshChannelCounts();
      await loadLatestHistory();
      $("status").textContent = `Posted #${data.message.id}`;
    } catch (error) {
      $("status").textContent = error.status === 401
        ? "Participant credential is no longer valid."
        : `Post failed: ${error.message}`;
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
    state.participantId = "";
    state.signingKey = null;
    state.identity = null;
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = null;
    $("identity").textContent = "Not connected";
    setConnected(false);
    $("connect-error").textContent = message || "";
  }

  $("connect").addEventListener("click", connect);
  $("participant-credential").addEventListener("keydown", (event) => {
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