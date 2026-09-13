PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS messages (
    id                 INTEGER PRIMARY KEY,
    created_at         INTEGER NOT NULL DEFAULT (unixepoch()),
    channel            TEXT NOT NULL,
    source             TEXT NOT NULL,
    instance           TEXT NOT NULL,
    conversation_ref  TEXT,
    kind               TEXT NOT NULL DEFAULT 'message',
    body               TEXT NOT NULL,
    reply_to           INTEGER
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

CREATE TABLE IF NOT EXISTS web_participants (
    participant_id     TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    label              TEXT,
    role               TEXT NOT NULL DEFAULT 'user',
    status             TEXT NOT NULL DEFAULT 'active',
    auth_scheme        TEXT,
    auth_secret        TEXT,
    totp_secret        TEXT,
    totp_last_step     INTEGER,
    totp_fail_count    INTEGER NOT NULL DEFAULT 0,
    totp_locked_until  INTEGER,
    owner_provider     TEXT,
    owner_subject      TEXT,
    owner_login        TEXT,
    created_at         INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at         INTEGER NOT NULL DEFAULT (unixepoch()),
    CHECK (role IN ('user', 'admin')),
    CHECK (status IN ('active', 'inactive')),
    CHECK (
        (auth_scheme IS NULL AND auth_secret IS NULL)
        OR (auth_scheme = 'hmac-sha256-v1' AND auth_secret IS NOT NULL)
    ),
    CHECK (
        (owner_provider IS NULL AND owner_subject IS NULL)
        OR (owner_provider IS NOT NULL AND owner_subject IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS channels (
    name        TEXT PRIMARY KEY,
    visibility  TEXT NOT NULL DEFAULT 'private',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    created_by  TEXT,
    CHECK (visibility IN ('public', 'private')),
    CHECK (status IN ('active', 'archived'))
);

CREATE TABLE IF NOT EXISTS navigation_writes (
    instance      TEXT NOT NULL,
    nonce         TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    message_id    INTEGER NOT NULL,
    created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (instance, nonce)
);

CREATE TABLE IF NOT EXISTS ingress_provenance (
    delivery_id         TEXT PRIMARY KEY,
    intent_id           TEXT NOT NULL,
    transport           TEXT NOT NULL,
    external_ref        TEXT NOT NULL,
    principal_provider  TEXT NOT NULL,
    principal_subject   TEXT NOT NULL,
    created_at          INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_ingress_provenance_intent
ON ingress_provenance(intent_id);

CREATE TABLE IF NOT EXISTS execution_receipts (
    participant_id  TEXT NOT NULL,
    intent_id       TEXT NOT NULL,
    intent_hash     TEXT NOT NULL,
    capability      TEXT NOT NULL,
    message_id      INTEGER NOT NULL,
    status          TEXT NOT NULL,
    created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (participant_id, intent_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_receipts_message
ON execution_receipts(message_id);
