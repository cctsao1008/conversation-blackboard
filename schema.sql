PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    channel     TEXT NOT NULL,
    source      TEXT NOT NULL,
    instance    TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'message',
    body        TEXT NOT NULL,
    reply_to    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_id
ON messages(channel, id);

CREATE TABLE IF NOT EXISTS identities (
    instance    TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    label       TEXT,
    token_hash  TEXT UNIQUE,
    created_at  INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS web_capabilities (
    instance         TEXT PRIMARY KEY,
    capability_hash  TEXT UNIQUE,
    created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at       INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS navigation_writes (
    instance      TEXT NOT NULL,
    nonce         TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    message_id    INTEGER NOT NULL,
    created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (instance, nonce)
);
