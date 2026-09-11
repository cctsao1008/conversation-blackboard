from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    if old not in text:
        raise SystemExit(f"expected text not found in {path}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


def sub_once(path, pattern, repl, flags=re.S):
    text = read(path)
    updated, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"expected one regex match in {path}, got {count}: {pattern[:120]!r}")
    write(path, updated)


# Human browser auth becomes TOTP + short-lived in-memory browser session.
write("src/web_auth.rs", r'''use std::{
    collections::BTreeMap,
    sync::OnceLock,
    time::{SystemTime, UNIX_EPOCH},
};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};
use ring::hmac;
use serde_json::{json, Value};
use subtle::ConstantTimeEq;
use url::Url;

use crate::signed_auth;

pub const WEB_SESSION_HEADER: &str = "x-blackboard-web-session";
pub const TOTP_PERIOD_SECS: u64 = 30;
pub const TOTP_DIGITS: u32 = 6;
pub const TOTP_MAX_FAILURES: i64 = 5;
pub const TOTP_LOCK_SECS: u64 = 60;
const WEB_SESSION_PREFIX: &str = "bbsess-v1";
const WEB_SESSION_PURPOSE: &str = "human-web-session-v1";
const WEB_SESSION_TTL_SECS: u64 = 8 * 60 * 60;
const BASE32_ALPHABET: &[u8; 32] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WebSession {
    pub participant_id: String,
    pub expires_at: u64,
    pub token: String,
}

pub fn current_unix_time() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub fn generate_totp_secret() -> String {
    let mut bytes = [0_u8; 20];
    OsRng.fill_bytes(&mut bytes);
    base32_encode(&bytes)
}

pub fn validate_totp_secret(secret: &str) -> bool {
    base32_decode(secret)
        .map(|bytes| (10..=64).contains(&bytes.len()))
        .unwrap_or(false)
}

pub fn otpauth_uri(participant_id: &str, secret: &str, issuer: &str) -> Option<String> {
    if participant_id.is_empty() || issuer.trim().is_empty() || !validate_totp_secret(secret) {
        return None;
    }
    let mut url = Url::parse("otpauth://totp/placeholder").ok()?;
    url.set_path(&format!("/{}:{}", issuer.trim(), participant_id));
    url.query_pairs_mut()
        .append_pair("secret", secret)
        .append_pair("issuer", issuer.trim())
        .append_pair("algorithm", "SHA1")
        .append_pair("digits", "6")
        .append_pair("period", "30");
    Some(url.into())
}

pub fn matching_totp_step(secret: &str, code: &str, now: u64) -> Option<u64> {
    if code.len() != TOTP_DIGITS as usize || !code.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let secret = base32_decode(secret)?;
    let current = now / TOTP_PERIOD_SECS;
    let mut candidates = [None; 3];
    candidates[0] = Some(current);
    candidates[1] = current.checked_sub(1);
    candidates[2] = current.checked_add(1);
    for step in candidates.into_iter().flatten() {
        let expected = totp_code_bytes(&secret, step, TOTP_DIGITS)?;
        if bool::from(expected.as_bytes().ct_eq(code.as_bytes())) {
            return Some(step);
        }
    }
    None
}

pub fn issue_web_session(participant_id: &str) -> WebSession {
    issue_web_session_at(participant_id, current_unix_time())
}

pub fn verify_web_session(token: &str) -> Option<WebSession> {
    verify_web_session_at(token, current_unix_time())
}

pub fn canonical_http_request_bytes(
    participant_id: &str,
    method: &str,
    request_target: &str,
) -> Vec<u8> {
    canonical_json([
        ("method", json!(method)),
        ("participant_id", json!(participant_id)),
        ("purpose", json!("http-request-auth-v1")),
        ("request_target", json!(request_target)),
        ("signature_version", json!(signed_auth::SIGNATURE_SCHEME)),
    ])
}

pub fn verify_http_request_signature(
    public_key: &str,
    signature: &str,
    participant_id: &str,
    method: &str,
    request_target: &str,
) -> bool {
    let payload = canonical_http_request_bytes(participant_id, method, request_target);
    signed_auth::verify_message_signature(public_key, signature, &payload)
}

fn issue_web_session_at(participant_id: &str, now: u64) -> WebSession {
    let expires_at = now.saturating_add(WEB_SESSION_TTL_SECS);
    let payload = canonical_json([
        ("expires_at", json!(expires_at)),
        ("participant_id", json!(participant_id)),
        ("purpose", json!(WEB_SESSION_PURPOSE)),
        ("version", json!(WEB_SESSION_PREFIX)),
    ]);
    let payload_encoded = URL_SAFE_NO_PAD.encode(&payload);
    let tag = hmac::sign(session_hmac_key(), &payload);
    let token = format!(
        "{WEB_SESSION_PREFIX}.{payload_encoded}.{}",
        URL_SAFE_NO_PAD.encode(tag.as_ref())
    );
    WebSession {
        participant_id: participant_id.to_owned(),
        expires_at,
        token,
    }
}

fn verify_web_session_at(token: &str, now: u64) -> Option<WebSession> {
    let mut parts = token.split('.');
    if parts.next()? != WEB_SESSION_PREFIX {
        return None;
    }
    let payload_encoded = parts.next()?;
    let tag_encoded = parts.next()?;
    if parts.next().is_some() {
        return None;
    }
    let payload = URL_SAFE_NO_PAD.decode(payload_encoded).ok()?;
    let tag = URL_SAFE_NO_PAD.decode(tag_encoded).ok()?;
    hmac::verify(session_hmac_key(), &payload, &tag).ok()?;
    let value: Value = serde_json::from_slice(&payload).ok()?;
    if value.get("version")?.as_str()? != WEB_SESSION_PREFIX
        || value.get("purpose")?.as_str()? != WEB_SESSION_PURPOSE
    {
        return None;
    }
    let participant_id = value.get("participant_id")?.as_str()?.to_owned();
    let expires_at = value.get("expires_at")?.as_u64()?;
    if participant_id.is_empty() || participant_id.len() > 64 || expires_at < now {
        return None;
    }
    Some(WebSession {
        participant_id,
        expires_at,
        token: token.to_owned(),
    })
}

fn session_hmac_key() -> &'static hmac::Key {
    static KEY: OnceLock<hmac::Key> = OnceLock::new();
    KEY.get_or_init(|| {
        let mut bytes = [0_u8; 32];
        OsRng.fill_bytes(&mut bytes);
        hmac::Key::new(hmac::HMAC_SHA256, &bytes)
    })
}

fn totp_code_bytes(secret: &[u8], step: u64, digits: u32) -> Option<String> {
    if !(6..=8).contains(&digits) {
        return None;
    }
    let key = hmac::Key::new(hmac::HMAC_SHA1_FOR_LEGACY_USE_ONLY, secret);
    let tag = hmac::sign(&key, &step.to_be_bytes());
    let digest = tag.as_ref();
    let offset = (digest.last()? & 0x0f) as usize;
    if offset + 4 > digest.len() {
        return None;
    }
    let binary = (u32::from(digest[offset] & 0x7f) << 24)
        | (u32::from(digest[offset + 1]) << 16)
        | (u32::from(digest[offset + 2]) << 8)
        | u32::from(digest[offset + 3]);
    let modulo = 10_u32.pow(digits);
    Some(format!("{:0width$}", binary % modulo, width = digits as usize))
}

fn base32_encode(bytes: &[u8]) -> String {
    let mut out = String::new();
    let mut buffer = 0_u32;
    let mut bits = 0_u8;
    for byte in bytes {
        buffer = (buffer << 8) | u32::from(*byte);
        bits += 8;
        while bits >= 5 {
            bits -= 5;
            let index = ((buffer >> bits) & 0x1f) as usize;
            out.push(BASE32_ALPHABET[index] as char);
        }
    }
    if bits > 0 {
        let index = ((buffer << (5 - bits)) & 0x1f) as usize;
        out.push(BASE32_ALPHABET[index] as char);
    }
    out
}

fn base32_decode(value: &str) -> Option<Vec<u8>> {
    if value.is_empty() || value.bytes().any(|byte| byte == b'=') {
        return None;
    }
    let mut out = Vec::new();
    let mut buffer = 0_u32;
    let mut bits = 0_u8;
    for byte in value.bytes() {
        let upper = byte.to_ascii_uppercase();
        let index = match upper {
            b'A'..=b'Z' => upper - b'A',
            b'2'..=b'7' => upper - b'2' + 26,
            _ => return None,
        };
        buffer = (buffer << 5) | u32::from(index);
        bits += 5;
        if bits >= 8 {
            bits -= 8;
            out.push(((buffer >> bits) & 0xff) as u8);
        }
    }
    Some(out)
}

fn canonical_json<const N: usize>(pairs: [(&str, Value); N]) -> Vec<u8> {
    let mut payload = BTreeMap::<String, Value>::new();
    for (key, value) in pairs {
        payload.insert(key.to_owned(), value);
    }
    serde_json::to_vec(&payload).expect("canonical auth payload must serialize")
}

#[cfg(test)]
pub fn totp_code_at(secret: &str, unix_time: u64, digits: u32) -> Option<String> {
    let secret = base32_decode(secret)?;
    totp_code_bytes(&secret, unix_time / TOTP_PERIOD_SECS, digits)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base32_round_trip_and_rfc6238_sha1_vector() {
        let raw = b"12345678901234567890";
        let secret = base32_encode(raw);
        assert_eq!(base32_decode(&secret).unwrap(), raw);
        assert_eq!(totp_code_at(&secret, 59, 8).as_deref(), Some("94287082"));
    }

    #[test]
    fn matching_totp_accepts_small_clock_skew() {
        let secret = generate_totp_secret();
        let now = 1_700_000_000;
        let code = totp_code_at(&secret, now, TOTP_DIGITS).unwrap();
        assert_eq!(matching_totp_step(&secret, &code, now), Some(now / TOTP_PERIOD_SECS));
        assert!(matching_totp_step(&secret, "00000x", now).is_none());
    }

    #[test]
    fn web_session_is_authenticated_and_expires() {
        let now = 1_700_000_000;
        let session = issue_web_session_at("single-main", now);
        let verified = verify_web_session_at(&session.token, now + 10).unwrap();
        assert_eq!(verified.participant_id, "single-main");
        assert!(verify_web_session_at(&session.token, session.expires_at + 1).is_none());
        let mut tampered = session.token;
        tampered.push('x');
        assert!(verify_web_session_at(&tampered, now).is_none());
    }
}
''')

write("src/request_auth.rs", r'''use axum::http::{header, HeaderMap};
use rusqlite::{Connection, Result};

use crate::{identity, model::Identity, signed_auth, web_auth};

pub const PARTICIPANT_ID_HEADER: &str = "x-blackboard-participant-id";
pub const SIGNATURE_SCHEME_HEADER: &str = "x-blackboard-signature-scheme";
pub const SIGNATURE_HEADER: &str = "x-blackboard-signature";
const RETIRED_PRIVATE_KEY_HEADER: &str = "x-blackboard-private-key";

pub fn resolve_request_identity_for_target(
    conn: &Connection,
    headers: &HeaderMap,
    method: &str,
    request_target: &str,
) -> Result<Option<Identity>> {
    let signed_attempt =
        headers.contains_key(SIGNATURE_HEADER) || headers.contains_key(SIGNATURE_SCHEME_HEADER);
    if !signed_attempt {
        return resolve_request_identity(conn, headers);
    }
    if headers.contains_key(RETIRED_PRIVATE_KEY_HEADER) || headers.contains_key(web_auth::WEB_SESSION_HEADER) {
        return Ok(None);
    }

    let Some(participant_id) = header_text(headers, PARTICIPANT_ID_HEADER) else {
        return Ok(None);
    };
    let Some(scheme) = header_text(headers, SIGNATURE_SCHEME_HEADER) else {
        return Ok(None);
    };
    let Some(signature) = header_text(headers, SIGNATURE_HEADER) else {
        return Ok(None);
    };
    if scheme != signed_auth::SIGNATURE_SCHEME {
        return Ok(None);
    }
    let Some(verification) = identity::get_web_participant_verification(conn, participant_id)? else {
        return Ok(None);
    };
    if verification.signature_scheme != scheme
        || !web_auth::verify_http_request_signature(
            &verification.public_key,
            signature,
            participant_id,
            method,
            request_target,
        )
    {
        return Ok(None);
    }
    Ok(Some(verification.identity))
}

pub fn resolve_request_identity(conn: &Connection, headers: &HeaderMap) -> Result<Option<Identity>> {
    if headers.contains_key(RETIRED_PRIVATE_KEY_HEADER) {
        return Ok(None);
    }

    if let Some(token) = header_text(headers, web_auth::WEB_SESSION_HEADER) {
        let Some(session) = web_auth::verify_web_session(token) else {
            return Ok(None);
        };
        return identity::get_web_participant(conn, &session.participant_id);
    }

    if headers.contains_key(PARTICIPANT_ID_HEADER)
        || headers.contains_key(SIGNATURE_HEADER)
        || headers.contains_key(SIGNATURE_SCHEME_HEADER)
    {
        return Ok(None);
    }

    let Some(token) = bearer_token(headers) else {
        return Ok(None);
    };
    identity::resolve_identity(conn, token)
}

fn header_text<'a>(headers: &'a HeaderMap, name: &'static str) -> Option<&'a str> {
    headers
        .get(name)?
        .to_str()
        .ok()
        .filter(|value| !value.is_empty())
}

fn bearer_token(headers: &HeaderMap) -> Option<&str> {
    let value = headers.get(header::AUTHORIZATION)?.to_str().ok()?;
    let (scheme, token) = value.split_once(' ')?;
    if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() {
        return None;
    }
    Some(token)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db;
    use tempfile::tempdir;

    #[test]
    fn web_session_and_bearer_resolve_without_raw_participant_keys() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("board.db");
        db::initialize(&path).unwrap();
        let conn = db::connect(&path).unwrap();
        identity::provision_web_participant_identity(
            &conn,
            "operator-main",
            "operator",
            Some("Operator browser"),
        )
        .unwrap()
        .unwrap();
        let (_, bearer) = identity::register_identity(&conn, "rest-client", Some("REST client")).unwrap();

        let session = web_auth::issue_web_session("operator-main");
        let mut headers = HeaderMap::new();
        headers.insert(web_auth::WEB_SESSION_HEADER, session.token.parse().unwrap());
        let resolved = resolve_request_identity(&conn, &headers).unwrap().unwrap();
        assert_eq!(resolved.instance, "operator-main");

        let mut retired = HeaderMap::new();
        retired.insert(RETIRED_PRIVATE_KEY_HEADER, "old-key".parse().unwrap());
        retired.insert(
            header::AUTHORIZATION,
            format!("Bearer {bearer}").parse().unwrap(),
        );
        assert!(resolve_request_identity(&conn, &retired).unwrap().is_none());

        let mut bearer_headers = HeaderMap::new();
        bearer_headers.insert(
            header::AUTHORIZATION,
            format!("Bearer {bearer}").parse().unwrap(),
        );
        assert_eq!(
            resolve_request_identity(&conn, &bearer_headers)
                .unwrap()
                .unwrap()
                .source,
            "rest-client"
        );
    }
}
''')

write("src/web_admin.rs", r'''use std::{error::Error, path::PathBuf};

use clap::Subcommand;

use crate::{db, identity, signed_auth, web_auth};

type DynResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Debug, Subcommand)]
pub enum WebCommand {
    /// Provision a participant identity for human TOTP and/or agent signing.
    Provision {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        source: String,
        #[arg(long)]
        label: Option<String>,
    },
    /// Generate and register a TOTP secret for human web login.
    TotpEnroll {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long, default_value = "ConversationBlackboard")]
        issuer: String,
    },
    /// Revoke human TOTP login for a participant.
    TotpRevoke {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
    /// Register or rotate the Ed25519 public signing key used by agents.
    SetSigningKey {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
        #[arg(long)]
        public_key: String,
    },
    /// Revoke the Ed25519 public signing key used by agents.
    RevokeSigningKey {
        #[arg(long)]
        db: PathBuf,
        #[arg(long)]
        participant_id: String,
    },
}

pub fn dispatch(command: WebCommand) -> DynResult {
    match command {
        WebCommand::Provision {
            db: path,
            participant_id,
            source,
            label,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            let record = identity::provision_web_participant_identity(
                &conn,
                &participant_id,
                &source,
                label.as_deref(),
            )?
            .ok_or_else(|| format!("participant already exists: {participant_id}"))?;
            println!("PARTICIPANT READY");
            println!("source         : {}", record.source);
            println!("participant_id : {}", record.instance);
            Ok(())
        }
        WebCommand::TotpEnroll {
            db: path,
            participant_id,
            issuer,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if identity::get_web_participant(&conn, &participant_id)?.is_none() {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            let secret = web_auth::generate_totp_secret();
            if !identity::set_web_participant_totp(&conn, &participant_id, &secret)? {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            let uri = web_auth::otpauth_uri(&participant_id, &secret, &issuer)
                .ok_or("could not construct TOTP enrollment URI")?;
            println!("TOTP ENROLLMENT READY");
            println!("participant_id : {participant_id}");
            println!("issuer         : {issuer}");
            println!("setup_key      : {secret}");
            println!("otpauth_uri     : {uri}");
            println!("note            : Add this account to any RFC 6238 authenticator, then use the 6-digit code on the web UI.");
            Ok(())
        }
        WebCommand::TotpRevoke {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !identity::revoke_web_participant_totp(&conn, &participant_id)? {
                return Err(format!("no active TOTP for participant: {participant_id}").into());
            }
            println!("revoked TOTP login: {participant_id}");
            Ok(())
        }
        WebCommand::SetSigningKey {
            db: path,
            participant_id,
            public_key,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if identity::get_web_participant(&conn, &participant_id)?.is_none() {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            if !signed_auth::validate_public_key(&public_key) {
                return Err("invalid Ed25519 public key material".into());
            }
            if !identity::set_web_participant_signing_key(&conn, &participant_id, &public_key)? {
                return Err(format!("unknown participant: {participant_id}").into());
            }
            println!("PARTICIPANT SIGNING KEY READY");
            println!("participant_id   : {participant_id}");
            println!("signature_scheme : {}", signed_auth::SIGNATURE_SCHEME);
            println!("public_key       : {public_key}");
            Ok(())
        }
        WebCommand::RevokeSigningKey {
            db: path,
            participant_id,
        } => {
            require_database(&path)?;
            let conn = db::connect(&path)?;
            if !identity::revoke_web_participant_signing_key(&conn, &participant_id)? {
                return Err(format!("no active signing key for participant: {participant_id}").into());
            }
            println!("revoked participant signing key: {participant_id}");
            Ok(())
        }
    }
}

fn require_database(path: &std::path::Path) -> DynResult {
    if path.is_file() {
        Ok(())
    } else {
        Err(format!("database does not exist: {}", path.display()).into())
    }
}
''')

write("web/index.html", r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>conversation-blackboard</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <main class="app-shell">
    <header class="topbar">
      <div>
        <h1>conversation-blackboard</h1>
        <p>Shared context across independent conversations.</p>
      </div>
      <div class="identity" id="identity">Not connected</div>
    </header>

    <section class="connect-panel" id="connect-panel">
      <label for="participant-id">Participant</label>
      <div class="connect-row">
        <input id="participant-id" type="text" autocomplete="username" spellcheck="false" aria-label="Participant ID" placeholder="cheng-main">
        <input id="totp-code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" aria-label="Authenticator code" placeholder="6-digit code">
        <button id="connect" type="button">Connect</button>
      </div>
      <p class="hint">Enter the current code from Google Authenticator or any RFC 6238 compatible authenticator.</p>
      <p class="error" id="connect-error" role="alert"></p>
    </section>

    <section class="board hidden" id="board">
      <aside class="sidebar">
        <div class="sidebar-head">
          <h2>Channels</h2>
          <button id="new-channel" type="button" class="secondary">New</button>
        </div>
        <div id="channels" class="channels" aria-label="Channels"></div>
      </aside>

      <section class="conversation">
        <header class="channel-head">
          <div class="channel-title">
            <span class="eyebrow">Channel</span>
            <h2 id="channel-name">Select a channel</h2>
          </div>
          <div class="channel-tools">
            <div class="channel-head-actions">
              <span id="channel-message-count" class="channel-message-count"></span>
              <button id="latest" type="button" class="secondary">Latest</button>
              <button id="refresh" type="button" class="secondary">Refresh</button>
            </div>
            <form id="jump-form" class="jump-form">
              <label for="jump-id">Jump to #</label>
              <input id="jump-id" type="number" min="1" step="1" inputmode="numeric" aria-label="Message ID">
              <button id="jump-go" type="submit" class="secondary">Go</button>
            </form>
          </div>
        </header>

        <div id="timeline" class="timeline" aria-live="polite"></div>
        <div id="empty" class="empty">No messages yet.</div>

        <form id="composer" class="composer">
          <div id="reply-bar" class="reply-bar hidden">
            <span id="reply-label"></span>
            <button id="cancel-reply" type="button" class="link-button">Cancel</button>
          </div>
          <div class="composer-meta">
            <label for="kind">Kind</label>
            <select id="kind">
              <option value="message">message</option>
              <option value="status">status</option>
              <option value="insight">insight</option>
              <option value="question">question</option>
              <option value="warning">warning</option>
              <option value="banter">banter</option>
            </select>
          </div>
          <textarea id="body" rows="4" placeholder="Leave a useful note for the other conversations..."></textarea>
          <div class="composer-actions">
            <span id="status" class="hint"></span>
            <button id="send" type="submit">Post</button>
          </div>
        </form>
      </section>
    </section>
  </main>

  <script src="/app.js" defer></script>
</body>
</html>
''')

# Browser code: remove all private-key/Ed25519 handling. Human auth is participant + TOTP.
app = read("web/app.js")
app = re.sub(
    r'''  const state = \{\n    participantId: "",\n    signingKey: null,\n    identity: null,''',
    '''  const state = {\n    participantId: "",\n    sessionToken: "",\n    identity: null,''',
    app,
    count=1,
)
app, n = re.subn(
    r'''\n  function base64urlDecode\(value\) \{.*?\n  async function api\(path, options = \{\}\) \{''',
    '''\n  async function api(path, options = {}) {''',
    app,
    count=1,
    flags=re.S,
)
if n != 1:
    raise SystemExit("failed to remove browser Ed25519 credential helpers")
app = app.replace(
    '''    const { skipRequestAuth = false, ...fetchOptions } = options;\n    const method = (fetchOptions.method || "GET").toUpperCase();\n    const headers = new Headers(fetchOptions.headers || {});\n    headers.set("Accept", "application/json");\n    if (!skipRequestAuth && method === "GET" && state.participantId && state.signingKey) {\n      const authHeaders = await signedReadHeaders(method, path);\n      for (const [name, value] of Object.entries(authHeaders)) headers.set(name, value);\n    }''',
    '''    const { skipSession = false, ...fetchOptions } = options;\n    const headers = new Headers(fetchOptions.headers || {});\n    headers.set("Accept", "application/json");\n    if (!skipSession && state.sessionToken) {\n      headers.set("X-Blackboard-Web-Session", state.sessionToken);\n    }''',
    1,
)
app, n = re.subn(
    r'''  async function connect\(\) \{.*?\n  \}\n\n  function channelButton''',
    r'''  async function connect() {
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

    try {
      const auth = await api("/api/auth/totp", {
        method: "POST",
        body: JSON.stringify({ participant_id: participantId, code }),
        skipSession: true,
      });
      state.participantId = auth.instance;
      state.sessionToken = auth.session_token;
      state.identity = auth;
      $("identity").textContent = identityText(state.identity);
      $("totp-code").value = "";
      setConnected(true);
      await loadChannels();
      startPolling();
    } catch (error) {
      state.participantId = "";
      state.sessionToken = "";
      state.identity = null;
      $("connect-error").textContent = error.status === 401
        ? "Participant or authenticator code was not accepted."
        : (error.message || "Could not connect.");
    }
  }

  function channelButton''',
    app,
    count=1,
    flags=re.S,
)
if n != 1:
    raise SystemExit("failed to replace connect()")
app = app.replace(
    '    if (!state.signingKey || !state.channel || !state.followLatest) return;',
    '    if (!state.sessionToken || !state.channel || !state.followLatest) return;',
    1,
)
app, n = re.subn(
    r'''      const payload = await signedWriteEnvelope\(\n        state.channel,\n        \$\("kind"\)\.value,\n        body,\n        state.replyTo \? state.replyTo.id : null,\n      \);\n      const data = await api\("/api/messages", \{\n        method: "POST",\n        body: JSON.stringify\(payload\),\n        skipRequestAuth: true,\n      \}\);''',
    '''      const data = await api("/api/messages", {\n        method: "POST",\n        body: JSON.stringify({\n          channel: state.channel,\n          kind: $("kind").value,\n          body,\n          reply_to: state.replyTo ? state.replyTo.id : null,\n        }),\n      });''',
    app,
    count=1,
)
if n != 1:
    raise SystemExit("failed to replace signed browser write")
app = app.replace(
    '''    state.participantId = "";\n    state.signingKey = null;\n    state.identity = null;''',
    '''    state.participantId = "";\n    state.sessionToken = "";\n    state.identity = null;''',
)
app = app.replace(
    '''  $("participant-credential").addEventListener("keydown", (event) => {\n    if (event.key === "Enter") connect();\n  });''',
    '''  $("participant-id").addEventListener("keydown", (event) => {\n    if (event.key === "Enter") $("totp-code").focus();\n  });\n  $("totp-code").addEventListener("keydown", (event) => {\n    if (event.key === "Enter") connect();\n  });''',
    1,
)
if "bbcred-v1" in app or "signingKey" in app or "crypto.subtle" in app:
    raise SystemExit("browser Ed25519 credential code still present")
write("web/app.js", app)

# Schema: new databases have TOTP columns and no legacy participant key_hash.
replace_once(
    "schema.sql",
    '''CREATE TABLE IF NOT EXISTS web_participants (\n    participant_id    TEXT PRIMARY KEY,\n    source            TEXT NOT NULL,\n    label             TEXT,\n    key_hash          TEXT UNIQUE,\n    public_key        TEXT,\n    signature_scheme  TEXT,\n    created_at        INTEGER NOT NULL DEFAULT (unixepoch()),\n    updated_at        INTEGER NOT NULL DEFAULT (unixepoch())\n);''',
    '''CREATE TABLE IF NOT EXISTS web_participants (\n    participant_id     TEXT PRIMARY KEY,\n    source             TEXT NOT NULL,\n    label              TEXT,\n    public_key         TEXT,\n    signature_scheme   TEXT,\n    totp_secret        TEXT,\n    totp_last_step     INTEGER,\n    totp_fail_count    INTEGER NOT NULL DEFAULT 0,\n    totp_locked_until  INTEGER,\n    created_at         INTEGER NOT NULL DEFAULT (unixepoch()),\n    updated_at         INTEGER NOT NULL DEFAULT (unixepoch())\n);''',
)

# Existing production databases get additive TOTP migration; any old key_hash column becomes inert.
replace_once(
    "src/db.rs",
    "    migrate_web_participant_signing_columns(&conn)?;",
    "    migrate_web_participant_signing_columns(&conn)?;\n    migrate_web_participant_totp_columns(&conn)?;",
)
insert_after = '''fn migrate_web_participant_signing_columns(conn: &Connection) -> Result<()> {\n    if !table_has_column(conn, "web_participants", "public_key")? {\n        conn.execute(\n            "ALTER TABLE web_participants ADD COLUMN public_key TEXT",\n            [],\n        )?;\n    }\n    if !table_has_column(conn, "web_participants", "signature_scheme")? {\n        conn.execute(\n            "ALTER TABLE web_participants ADD COLUMN signature_scheme TEXT",\n            [],\n        )?;\n    }\n    Ok(())\n}\n'''
replace_once(
    "src/db.rs",
    insert_after,
    insert_after + '''\nfn migrate_web_participant_totp_columns(conn: &Connection) -> Result<()> {\n    for (name, definition) in [\n        ("totp_secret", "TEXT"),\n        ("totp_last_step", "INTEGER"),\n        ("totp_fail_count", "INTEGER NOT NULL DEFAULT 0"),\n        ("totp_locked_until", "INTEGER"),\n    ] {\n        if !table_has_column(conn, "web_participants", name)? {\n            conn.execute(\n                &format!("ALTER TABLE web_participants ADD COLUMN {name} {definition}"),\n                [],\n            )?;\n        }\n    }\n    Ok(())\n}\n''',
)
replace_once(
    "src/db.rs",
    '''        assert!(table_has_column(&conn, "web_participants", "signature_scheme").unwrap());''',
    '''        assert!(table_has_column(&conn, "web_participants", "signature_scheme").unwrap());\n        assert!(table_has_column(&conn, "web_participants", "totp_secret").unwrap());\n        assert!(table_has_column(&conn, "web_participants", "totp_last_step").unwrap());\n        assert!(table_has_column(&conn, "web_participants", "totp_fail_count").unwrap());\n        assert!(table_has_column(&conn, "web_participants", "totp_locked_until").unwrap());''',
)

# Participant identity gains TOTP enrollment, verification, replay prevention, and throttling.
replace_once(
    "src/identity.rs",
    "use crate::{model::Identity, participant_key, signed_auth};",
    "use crate::{model::Identity, participant_key, signed_auth, web_auth};",
)
anchor = '''pub fn get_web_participant(conn: &Connection, participant_id: &str) -> Result<Option<Identity>> {\n    conn.query_row(\n        "SELECT source, participant_id, label\\n         FROM web_participants\\n         WHERE participant_id = ?1\\n         LIMIT 1",\n        [participant_id],\n        |row| {\n            Ok(Identity {\n                source: row.get(0)?,\n                instance: row.get(1)?,\n                label: row.get(2)?,\n            })\n        },\n    )\n    .optional()\n}\n'''
addition = r'''

pub fn provision_web_participant_identity(
    conn: &Connection,
    participant_id: &str,
    source: &str,
    label: Option<&str>,
) -> Result<Option<Identity>> {
    let participant_id =
        validate_participant_id(participant_id).ok_or(rusqlite::Error::InvalidQuery)?;
    let source = validate_source(source).ok_or(rusqlite::Error::InvalidQuery)?;
    let label = normalize_label(label).map_err(|_| rusqlite::Error::InvalidQuery)?;
    if get_web_participant(conn, &participant_id)?.is_some() {
        return Ok(None);
    }
    conn.execute(
        "INSERT INTO web_participants (participant_id, source, label) VALUES (?1, ?2, ?3)",
        params![participant_id, source, label],
    )?;
    Ok(Some(Identity {
        source,
        instance: participant_id,
        label,
    }))
}

pub fn set_web_participant_totp(
    conn: &Connection,
    participant_id: &str,
    secret: &str,
) -> Result<bool> {
    if validate_participant_id(participant_id).is_none() || !web_auth::validate_totp_secret(secret) {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants
         SET totp_secret = ?1, totp_last_step = NULL, totp_fail_count = 0,
             totp_locked_until = NULL, updated_at = unixepoch()
         WHERE participant_id = ?2",
        params![secret, participant_id],
    )?;
    Ok(changed == 1)
}

pub fn revoke_web_participant_totp(conn: &Connection, participant_id: &str) -> Result<bool> {
    if validate_participant_id(participant_id).is_none() {
        return Err(rusqlite::Error::InvalidQuery);
    }
    let changed = conn.execute(
        "UPDATE web_participants
         SET totp_secret = NULL, totp_last_step = NULL, totp_fail_count = 0,
             totp_locked_until = NULL, updated_at = unixepoch()
         WHERE participant_id = ?1 AND totp_secret IS NOT NULL",
        [participant_id],
    )?;
    Ok(changed == 1)
}

pub fn authenticate_web_totp(
    conn: &Connection,
    participant_id: &str,
    code: &str,
    now: u64,
) -> Result<Option<Identity>> {
    if validate_participant_id(participant_id).is_none()
        || code.len() != web_auth::TOTP_DIGITS as usize
        || !code.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Ok(None);
    }

    let tx = conn.unchecked_transaction()?;
    let row: Option<(String, Option<String>, String, Option<i64>, i64, Option<i64>)> = tx
        .query_row(
            "SELECT source, label, totp_secret, totp_last_step, totp_fail_count, totp_locked_until
             FROM web_participants
             WHERE participant_id = ?1 AND totp_secret IS NOT NULL
             LIMIT 1",
            [participant_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                ))
            },
        )
        .optional()?;
    let Some((source, label, secret, last_step, fail_count, locked_until)) = row else {
        return Ok(None);
    };

    let now_i64 = i64::try_from(now).unwrap_or(i64::MAX);
    if locked_until.is_some_and(|until| until > now_i64) {
        return Ok(None);
    }

    if let Some(step) = web_auth::matching_totp_step(&secret, code, now) {
        let step_i64 = i64::try_from(step).unwrap_or(i64::MAX);
        if last_step.is_some_and(|last| step_i64 <= last) {
            return Ok(None);
        }
        tx.execute(
            "UPDATE web_participants
             SET totp_last_step = ?1, totp_fail_count = 0, totp_locked_until = NULL,
                 updated_at = unixepoch()
             WHERE participant_id = ?2",
            params![step_i64, participant_id],
        )?;
        tx.commit()?;
        return Ok(Some(Identity {
            source,
            instance: participant_id.to_owned(),
            label,
        }));
    }

    let effective_fail_count = if locked_until.is_some() { 0 } else { fail_count };
    let new_fail_count = effective_fail_count.saturating_add(1);
    if new_fail_count >= web_auth::TOTP_MAX_FAILURES {
        let locked_until = now.saturating_add(web_auth::TOTP_LOCK_SECS);
        tx.execute(
            "UPDATE web_participants
             SET totp_fail_count = 0, totp_locked_until = ?1, updated_at = unixepoch()
             WHERE participant_id = ?2",
            params![i64::try_from(locked_until).unwrap_or(i64::MAX), participant_id],
        )?;
    } else {
        tx.execute(
            "UPDATE web_participants
             SET totp_fail_count = ?1, totp_locked_until = NULL, updated_at = unixepoch()
             WHERE participant_id = ?2",
            params![new_fail_count, participant_id],
        )?;
    }
    tx.commit()?;
    Ok(None)
}
'''
replace_once("src/identity.rs", anchor, anchor + addition)

# HTTP: TOTP login replaces browser challenge/signature login. Navigation raw-key auth is removed.
replace_once(
    "src/http.rs",
    '''        .route("/api/auth/challenge", post(auth_challenge))\n        .route("/api/auth/verify", post(auth_verify))''',
    '''        .route("/api/auth/totp", post(auth_totp))''',
)
sub_once(
    "src/http.rs",
    r'''    let signed_attempt = params\.contains_key\("sig"\) \|\| params\.contains_key\("scheme"\);.*?    let request_hash = identity::hash_token\(''',
    r'''    if params.contains_key("key") {
        return Err(ApiError::unauthorized());
    }
    let scheme = params
        .get("scheme")
        .filter(|value| value.as_str() == signed_auth::SIGNATURE_SCHEME)
        .ok_or_else(ApiError::unauthorized)?
        .clone();
    let signature = params
        .get("sig")
        .filter(|value| !value.is_empty() && value.len() <= 256)
        .cloned()
        .ok_or_else(ApiError::unauthorized)?;
    let lookup_participant = participant_id.clone();
    let verify_channel = channel.clone();
    let verify_kind = kind.clone();
    let verify_body = message_body.clone();
    let verify_nonce = nonce.clone();
    let write_identity = with_db(&state, move |conn| {
        let Some(record) = identity::get_web_participant_verification(conn, &lookup_participant)? else {
            return Ok(None);
        };
        if record.signature_scheme != scheme
            || !signed_auth::verify_write_signature(
                &record.public_key,
                &signature,
                &lookup_participant,
                &verify_channel,
                &verify_kind,
                &verify_body,
                reply_to,
                &verify_nonce,
            )
        {
            return Ok(None);
        }
        Ok(Some(record.identity))
    })
    .await?
    .ok_or_else(ApiError::unauthorized)?;

    let request_hash = identity::hash_token(''',
)
sub_once(
    "src/http.rs",
    r'''async fn auth_challenge\(.*?\nasync fn whoami''',
    r'''async fn auth_totp(
    State(state): State<AppState>,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    let body = read_json_object(request).await?;
    if !only_keys(&body, &["participant_id", "code"]) {
        return Err(ApiError::new(StatusCode::BAD_REQUEST, "invalid_auth"));
    }
    let participant_id = body
        .get("participant_id")
        .and_then(Value::as_str)
        .and_then(identity::validate_participant_id)
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_participant_id"))?;
    let code = body
        .get("code")
        .and_then(Value::as_str)
        .filter(|value| value.len() == web_auth::TOTP_DIGITS as usize)
        .filter(|value| value.bytes().all(|byte| byte.is_ascii_digit()))
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "invalid_auth"))?
        .to_owned();
    let lookup = participant_id.clone();
    let now = web_auth::current_unix_time();
    let verified = with_db(&state, move |conn| {
        identity::authenticate_web_totp(conn, &lookup, &code, now)
    })
    .await?
    .ok_or_else(ApiError::unauthorized)?;

    let session = web_auth::issue_web_session(&participant_id);
    Ok(json_response(
        StatusCode::OK,
        json!({
            "source": verified.source,
            "instance": verified.instance,
            "label": verified.label,
            "session_token": session.token,
            "expires_at": session.expires_at,
        }),
    ))
}

async fn whoami''',
)

# MCP is Ed25519-only for participant writes.
replace_once(
    "src/mcp.rs",
    '"instructions": "Read shared channels with blackboard_read. Use blackboard_write only when the user wants to append a thought as an approved Participant ID. Signed Ed25519 requests are preferred; legacy private-key writes remain temporarily available during migration. The server owns source/instance provenance. Use authoritative message IDs for reply_to; exact retries with the same nonce are idempotent."',
    '"instructions": "Read shared channels with blackboard_read. Use blackboard_write only when the user wants to append a thought as an approved Participant ID. Participant writes require an ed25519-v1 signature over the canonical write request. The server owns source/instance provenance. Use authoritative message IDs for reply_to; exact retries with the same nonce are idempotent."',
)
replace_once(
    "src/mcp.rs",
    '"description": "Append a thought as an approved participant. Prefer participant_id plus an ed25519-v1 signature over the canonical write request. Legacy participant_id plus private_key remains temporarily available during migration. The server resolves provenance; exact retries with the same nonce are idempotent."',
    '"description": "Append a thought as an approved participant using participant_id plus an ed25519-v1 signature over the canonical write request. The server resolves provenance; exact retries with the same nonce are idempotent."',
)
sub_once(
    "src/mcp.rs",
    r'''\n                        "private_key": \{.*?\n                        \},\n                        "auth":''',
    '\n                        "auth":',
)
replace_once(
    "src/mcp.rs",
    '''                    "required": ["participant_id", "channel", "body", "nonce"],''',
    '''                    "required": ["participant_id", "auth", "channel", "body", "nonce"],''',
)
replace_once(
    "src/mcp.rs",
    '''            "participant_id",\n            "private_key",\n            "auth",''',
    '''            "participant_id",\n            "auth",''',
)
sub_once(
    "src/mcp.rs",
    r'''#\[allow\(clippy::too_many_arguments\)\]\nasync fn resolve_write_identity\(.*?\n\}\n\nasync fn with_db''',
    r'''#[allow(clippy::too_many_arguments)]
async fn resolve_write_identity(
    state: &AppState,
    arguments: &Map<String, Value>,
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> Result<Identity, &'static str> {
    let auth = arguments
        .get("auth")
        .and_then(Value::as_object)
        .ok_or("invalid_auth")?;
    if auth.len() != 2 || !only_keys(auth, &["scheme", "signature"]) {
        return Err("invalid_auth");
    }
    let scheme = auth
        .get("scheme")
        .and_then(Value::as_str)
        .ok_or("invalid_auth")?;
    if scheme != signed_auth::SIGNATURE_SCHEME {
        return Err("unsupported_signature_scheme");
    }
    let signature = auth
        .get("signature")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 128)
        .ok_or("invalid_auth")?;

    let lookup_participant = participant_id.to_owned();
    let verification = match with_db(state, move |conn| {
        identity::get_web_participant_verification(conn, &lookup_participant)
    })
    .await
    {
        Ok(Some(verification)) => verification,
        Ok(None) => return Err("unauthorized"),
        Err(()) => return Err("database_unavailable"),
    };
    if verification.signature_scheme != signed_auth::SIGNATURE_SCHEME {
        return Err("unsupported_signature_scheme");
    }
    if !signed_auth::verify_write_signature(
        &verification.public_key,
        signature,
        participant_id,
        channel,
        kind,
        body,
        reply_to,
        nonce,
    ) {
        return Err("unauthorized");
    }
    Ok(verification.identity)
}

async fn with_db''',
)

# CLI descriptions and CI smoke no longer mention raw web private keys.
replace_once(
    "src/main.rs",
    "    /// Provision, rotate, or revoke web Participant IDs and prompt-held keys.",
    "    /// Manage participant identities, human TOTP login, and agent signing keys.",
)

ci = read(".github/workflows/ci.yml")
ci, n = re.subn(
    r'''            \$web = \(& \$exe web provision --db \$db --participant-id ci-web-main --source ci-web --label "CI web participant" --key ci-web-key-a\) \| Out-String\n            if \(\$web -notmatch 'participant_id\\s\*:\\s\*ci-web-main' -or \$web -notmatch 'private_key\\s\*:\\s\*ci-web-key-a'\) \{\n              throw "web participant provisioning output mismatch"\n            \}\n            \$rotatedWeb = \(& \$exe web rotate --db \$db --participant-id ci-web-main --key ci-web-key-b\) \| Out-String\n            if \(\$rotatedWeb -notmatch 'private_key\\s\*:\\s\*ci-web-key-b'\) \{\n              throw "web participant rotation output mismatch"\n            \}\n            & \$exe web revoke --db \$db --participant-id ci-web-main''',
    '''            $web = (& $exe web provision --db $db --participant-id ci-web-main --source ci-web --label "CI web participant") | Out-String
            if ($web -notmatch 'participant_id\\s*:\\s*ci-web-main') {
              throw "web participant provisioning output mismatch"
            }
            $totp = (& $exe web totp-enroll --db $db --participant-id ci-web-main) | Out-String
            if ($totp -notmatch 'setup_key\\s*:' -or $totp -notmatch 'otpauth_uri\\s*:') {
              throw "TOTP enrollment output mismatch"
            }
            & $exe web totp-revoke --db $db --participant-id ci-web-main''',
    ci,
    count=1,
)
if n != 1:
    raise SystemExit("failed to update Windows CI web smoke")
ci = ci.replace(
    '          .\\target\\release\\conversation-blackboard.exe web provision --help\n',
    '          .\\target\\release\\conversation-blackboard.exe web provision --help\n          .\\target\\release\\conversation-blackboard.exe web totp-enroll --help\n          .\\target\\release\\conversation-blackboard.exe web totp-revoke --help\n',
    1,
)
write(".github/workflows/ci.yml", ci)

# Focused browser TOTP contract tests.
write("src/web_auth_contract_tests.rs", r'''use std::path::PathBuf;

use axum::{
    body::{to_bytes, Body},
    http::{header, HeaderMap, Method, Request, StatusCode},
    Router,
};
use serde_json::{json, Value};
use tempfile::{tempdir, TempDir};
use tower::ServiceExt;

use crate::{db, http, identity, web_auth};

struct Fixture {
    _dir: TempDir,
    db_path: PathBuf,
    router: Router,
    participant_id: String,
    secret: String,
}

fn fixture() -> Fixture {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();
    identity::provision_web_participant_identity(
        &conn,
        "browser-main",
        "browser",
        Some("Browser participant"),
    )
    .unwrap()
    .unwrap();
    let secret = web_auth::generate_totp_secret();
    identity::set_web_participant_totp(&conn, "browser-main", &secret).unwrap();
    drop(conn);

    let router = http::app(http::AppState {
        db_path: db_path.clone(),
        registration_key: None,
    });
    Fixture {
        _dir: dir,
        db_path,
        router,
        participant_id: "browser-main".to_owned(),
        secret,
    }
}

async fn json_request(
    router: &Router,
    method: Method,
    uri: &str,
    body: Option<Value>,
    headers: &[(&str, String)],
) -> (StatusCode, Value) {
    let mut builder = Request::builder().method(method).uri(uri);
    if body.is_some() {
        builder = builder.header(header::CONTENT_TYPE, "application/json");
    }
    for (name, value) in headers {
        builder = builder.header(*name, value);
    }
    let request_body = body
        .map(|value| Body::from(value.to_string()))
        .unwrap_or_else(Body::empty);
    let response = router
        .clone()
        .oneshot(builder.body(request_body).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 256 * 1024).await.unwrap();
    let value = serde_json::from_slice(&bytes).unwrap_or_else(|_| json!({}));
    (status, value)
}

#[tokio::test]
async fn browser_totp_login_issues_session_for_normal_reads_and_writes() {
    let fixture = fixture();
    let now = web_auth::current_unix_time();
    let code = web_auth::totp_code_at(&fixture.secret, now, web_auth::TOTP_DIGITS).unwrap();
    let (status, auth) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/totp",
        Some(json!({"participant_id": fixture.participant_id, "code": code})),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(auth["source"], "browser");
    assert_eq!(auth["instance"], "browser-main");
    let session = auth["session_token"].as_str().unwrap().to_owned();
    let headers = [(web_auth::WEB_SESSION_HEADER, session)];

    let (status, _) = json_request(&fixture.router, Method::GET, "/api/channels", None, &headers).await;
    assert_eq!(status, StatusCode::OK);

    let (status, written) = json_request(
        &fixture.router,
        Method::POST,
        "/api/messages",
        Some(json!({
            "channel": "human-web",
            "kind": "message",
            "body": "TOTP-authenticated human write",
            "reply_to": null
        })),
        &headers,
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(written["message"]["instance"], "browser-main");

    let conn = db::connect(&fixture.db_path).unwrap();
    let rows = db::list_messages_after(&conn, 0, Some("human-web"), 10).unwrap();
    assert_eq!(rows.len(), 1);
}

#[tokio::test]
async fn totp_code_is_one_time_per_time_step_and_old_browser_endpoints_are_gone() {
    let fixture = fixture();
    let now = web_auth::current_unix_time();
    let code = web_auth::totp_code_at(&fixture.secret, now, web_auth::TOTP_DIGITS).unwrap();
    let body = json!({"participant_id": fixture.participant_id, "code": code});
    let (first, _) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/totp",
        Some(body.clone()),
        &[],
    )
    .await;
    assert_eq!(first, StatusCode::OK);
    let (replay, _) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/totp",
        Some(body),
        &[],
    )
    .await;
    assert_eq!(replay, StatusCode::UNAUTHORIZED);

    let (old_challenge, _) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/challenge",
        Some(json!({"participant_id": "browser-main"})),
        &[],
    )
    .await;
    assert_eq!(old_challenge, StatusCode::NOT_FOUND);

    let app = include_str!("../web/app.js");
    assert!(!app.contains("bbcred-v1"));
    assert!(!app.contains("crypto.subtle"));
    assert!(!app.contains("private_key"));
    assert!(app.contains("/api/auth/totp"));
}

#[tokio::test]
async fn repeated_bad_codes_are_throttled_without_disclosing_participant_state() {
    let fixture = fixture();
    for _ in 0..web_auth::TOTP_MAX_FAILURES {
        let (status, _) = json_request(
            &fixture.router,
            Method::POST,
            "/api/auth/totp",
            Some(json!({"participant_id": fixture.participant_id, "code": "000000"})),
            &[],
        )
        .await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
    }
    let now = web_auth::current_unix_time();
    let valid = web_auth::totp_code_at(&fixture.secret, now, web_auth::TOTP_DIGITS).unwrap();
    let (status, _) = json_request(
        &fixture.router,
        Method::POST,
        "/api/auth/totp",
        Some(json!({"participant_id": fixture.participant_id, "code": valid})),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}
''')

# Rewrite MCP contract tests around the final Ed25519-only contract.
write("src/mcp_contract_tests.rs", r'''use std::path::PathBuf;

use axum::{
    body::{to_bytes, Body},
    http::{header, Method, Request, StatusCode},
    response::Response,
    Router,
};
use serde_json::{json, Value};
use tempfile::{tempdir, TempDir};
use tower::ServiceExt;

use crate::{db, http::AppState, identity, mcp, signed_auth};

struct Fixture {
    _dir: TempDir,
    db_path: PathBuf,
    router: Router,
    single_signing_private: String,
    rotary_signing_private: String,
}

fn fixture() -> Fixture {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();
    identity::provision_web_participant_identity(&conn, "single-main", "single", Some("Single"))
        .unwrap()
        .unwrap();
    identity::provision_web_participant_identity(&conn, "rotary-main", "rotary", Some("Rotary"))
        .unwrap()
        .unwrap();
    let (single_signing_private, single_public) = signed_auth::generate_keypair();
    let (rotary_signing_private, rotary_public) = signed_auth::generate_keypair();
    identity::set_web_participant_signing_key(&conn, "single-main", &single_public).unwrap();
    identity::set_web_participant_signing_key(&conn, "rotary-main", &rotary_public).unwrap();
    drop(conn);
    let router = mcp::app(AppState {
        db_path: db_path.clone(),
        registration_key: None,
    });
    Fixture {
        _dir: dir,
        db_path,
        router,
        single_signing_private,
        rotary_signing_private,
    }
}

fn signed_write_arguments(
    private_key: &str,
    participant_id: &str,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> Value {
    let signature = signed_auth::sign_write(
        private_key,
        participant_id,
        channel,
        kind,
        body,
        reply_to,
        nonce,
    )
    .unwrap();
    json!({
        "participant_id": participant_id,
        "channel": channel,
        "kind": kind,
        "body": body,
        "reply_to": reply_to,
        "nonce": nonce,
        "auth": {"scheme": signed_auth::SIGNATURE_SCHEME, "signature": signature}
    })
}

async fn request(router: &Router, method: Method, body: Option<Value>) -> Response {
    let mut builder = Request::builder().method(method).uri("/mcp");
    if body.is_some() {
        builder = builder
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::ACCEPT, "application/json, text/event-stream")
            .header("mcp-protocol-version", "2025-11-25");
    }
    let body = body
        .map(|value| Body::from(value.to_string()))
        .unwrap_or_else(Body::empty);
    router.clone().oneshot(builder.body(body).unwrap()).await.unwrap()
}

async fn response_json(response: Response) -> (StatusCode, Value) {
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 256 * 1024).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

async fn call_tool(router: &Router, id: i64, name: &str, arguments: Value) -> Value {
    let response = request(
        router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments}
        })),
    )
    .await;
    let (status, value) = response_json(response).await;
    assert_eq!(status, StatusCode::OK);
    value
}

fn tool_error_code(value: &Value) -> &str {
    value["result"]["content"][0]["text"].as_str().unwrap()
}

#[tokio::test]
async fn mcp_advertises_signed_only_write_contract() {
    let fixture = fixture();
    let response = request(
        &fixture.router,
        Method::POST,
        Some(json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {}
        })),
    )
    .await;
    let (_, value) = response_json(response).await;
    let tools = value["result"]["tools"].as_array().unwrap();
    assert_eq!(tools.len(), 2);
    let write = &tools[1];
    assert_eq!(write["name"], "blackboard_write");
    assert!(write["inputSchema"]["properties"]["auth"].is_object());
    assert!(write["inputSchema"]["properties"]["private_key"].is_null());
    assert!(write["inputSchema"]["required"]
        .as_array()
        .unwrap()
        .iter()
        .any(|value| value == "auth"));
}

#[tokio::test]
async fn signed_write_is_verified_idempotent_and_readable() {
    let fixture = fixture();
    let arguments = signed_write_arguments(
        &fixture.single_signing_private,
        "single-main",
        "signed-architecture",
        "insight",
        "Private key stays with participant.",
        None,
        "signed-single-001",
    );
    let first = call_tool(&fixture.router, 10, "blackboard_write", arguments.clone()).await;
    assert_eq!(first["result"]["isError"], false);
    let created = &first["result"]["structuredContent"];
    assert_eq!(created["status"], "created");
    assert_eq!(created["source"], "single");
    let id = created["id"].as_i64().unwrap();

    let replay = call_tool(&fixture.router, 11, "blackboard_write", arguments).await;
    assert_eq!(replay["result"]["structuredContent"]["status"], "existing");
    assert_eq!(replay["result"]["structuredContent"]["id"].as_i64(), Some(id));

    let read = call_tool(
        &fixture.router,
        12,
        "blackboard_read",
        json!({"channel": "signed-architecture", "after": 0, "limit": 50}),
    )
    .await;
    assert_eq!(read["result"]["structuredContent"]["count"], 1);
}

#[tokio::test]
async fn write_rejects_tampering_wrong_identity_missing_auth_and_retired_private_key() {
    let fixture = fixture();
    let original = signed_write_arguments(
        &fixture.single_signing_private,
        "single-main",
        "signed-security",
        "message",
        "original body",
        None,
        "signed-security-001",
    );
    let mut tampered = original.clone();
    tampered["body"] = json!("tampered body");
    let response = call_tool(&fixture.router, 20, "blackboard_write", tampered).await;
    assert_eq!(tool_error_code(&response), "unauthorized");

    let wrong = signed_write_arguments(
        &fixture.single_signing_private,
        "rotary-main",
        "signed-security",
        "message",
        "wrong signer",
        None,
        "signed-security-002",
    );
    let response = call_tool(&fixture.router, 21, "blackboard_write", wrong).await;
    assert_eq!(tool_error_code(&response), "unauthorized");

    let response = call_tool(
        &fixture.router,
        22,
        "blackboard_write",
        json!({
            "participant_id": "single-main",
            "channel": "signed-security",
            "body": "missing auth",
            "nonce": "missing-auth"
        }),
    )
    .await;
    assert_eq!(tool_error_code(&response), "invalid_auth");

    let response = call_tool(
        &fixture.router,
        23,
        "blackboard_write",
        json!({
            "participant_id": "single-main",
            "private_key": "retired",
            "channel": "signed-security",
            "body": "retired raw key",
            "nonce": "retired-key"
        }),
    )
    .await;
    assert_eq!(tool_error_code(&response), "invalid_arguments");
}

#[tokio::test]
async fn signed_write_honors_rotation_revocation_nonce_and_reply_provenance() {
    let fixture = fixture();
    let single = signed_write_arguments(
        &fixture.single_signing_private,
        "single-main",
        "conversation-architecture",
        "idea",
        "Single thought",
        None,
        "single-target",
    );
    let first = call_tool(&fixture.router, 30, "blackboard_write", single).await;
    let single_id = first["result"]["structuredContent"]["id"].as_i64().unwrap();

    let rotary = signed_write_arguments(
        &fixture.rotary_signing_private,
        "rotary-main",
        "conversation-architecture",
        "insight",
        "Rotary reply",
        Some(single_id),
        "rotary-reply",
    );
    let response = call_tool(&fixture.router, 31, "blackboard_write", rotary).await;
    assert_eq!(response["result"]["structuredContent"]["source"], "rotary");
    assert_eq!(response["result"]["structuredContent"]["reply_to"].as_i64(), Some(single_id));

    let (new_private, new_public) = signed_auth::generate_keypair();
    let conn = db::connect(&fixture.db_path).unwrap();
    identity::set_web_participant_signing_key(&conn, "rotary-main", &new_public).unwrap();
    drop(conn);
    let old = signed_write_arguments(
        &fixture.rotary_signing_private,
        "rotary-main",
        "signed-lifecycle",
        "message",
        "old key",
        None,
        "old-key",
    );
    assert_eq!(tool_error_code(&call_tool(&fixture.router, 32, "blackboard_write", old).await), "unauthorized");

    let first = signed_write_arguments(
        &new_private,
        "rotary-main",
        "signed-lifecycle",
        "message",
        "new key",
        None,
        "same-nonce",
    );
    assert_eq!(call_tool(&fixture.router, 33, "blackboard_write", first).await["result"]["isError"], false);
    let conflict = signed_write_arguments(
        &new_private,
        "rotary-main",
        "signed-lifecycle",
        "message",
        "different",
        None,
        "same-nonce",
    );
    assert_eq!(tool_error_code(&call_tool(&fixture.router, 34, "blackboard_write", conflict).await), "nonce_conflict");

    let conn = db::connect(&fixture.db_path).unwrap();
    identity::revoke_web_participant_signing_key(&conn, "rotary-main").unwrap();
    drop(conn);
    let revoked = signed_write_arguments(
        &new_private,
        "rotary-main",
        "signed-lifecycle",
        "message",
        "revoked",
        None,
        "revoked",
    );
    assert_eq!(tool_error_code(&call_tool(&fixture.router, 35, "blackboard_write", revoked).await), "unauthorized");
}

#[tokio::test]
async fn mcp_transport_remains_stateless_and_rejects_unknown_origins() {
    let fixture = fixture();
    let get_response = request(&fixture.router, Method::GET, None).await;
    assert_eq!(get_response.status(), StatusCode::METHOD_NOT_ALLOWED);

    let invalid_origin = fixture
        .router
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/mcp")
                .header(header::CONTENT_TYPE, "application/json")
                .header(header::ACCEPT, "application/json, text/event-stream")
                .header(header::ORIGIN, "https://evil.example")
                .body(Body::from(
                    json!({"jsonrpc": "2.0", "id": 40, "method": "tools/list", "params": {}})
                        .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(invalid_origin.status(), StatusCode::FORBIDDEN);
}
''')

# Rewrite HTTP contract tests around signed navigation; raw-key /w is retired.
write("src/http_contract_tests.rs", r'''use std::path::PathBuf;

use axum::{
    body::{to_bytes, Body},
    http::{HeaderMap, Request, StatusCode},
    response::Response,
    Router,
};
use serde_json::Value;
use tempfile::{tempdir, TempDir};
use tower::ServiceExt;

use crate::{db, http::{self, AppState}, identity, model::Identity, signed_auth};

struct Fixture {
    _dir: TempDir,
    db_path: PathBuf,
    router: Router,
    identity: Identity,
    participant_id: String,
    signing_private: String,
}

fn fixture(source: &str) -> Fixture {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("board.db");
    db::initialize(&db_path).unwrap();
    let conn = db::connect(&db_path).unwrap();
    let participant_id = format!("{source}-main");
    let identity = identity::provision_web_participant_identity(
        &conn,
        &participant_id,
        source,
        Some("http-test"),
    )
    .unwrap()
    .unwrap();
    let (signing_private, signing_public) = signed_auth::generate_keypair();
    identity::set_web_participant_signing_key(&conn, &participant_id, &signing_public).unwrap();
    drop(conn);
    let router = http::app(AppState { db_path: db_path.clone(), registration_key: None });
    Fixture { _dir: dir, db_path, router, identity, participant_id, signing_private }
}

async fn get(router: &Router, uri: &str) -> Response {
    router.clone().oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap()).await.unwrap()
}

async fn response_text(response: Response) -> (StatusCode, HeaderMap, String) {
    let (parts, body) = response.into_parts();
    let bytes = to_bytes(body, http::MAX_BODY_BYTES * 2).await.unwrap();
    (parts.status, parts.headers, String::from_utf8(bytes.to_vec()).unwrap())
}

fn message_json(body: &str) -> Value {
    let line = body.lines().find(|line| line.starts_with('{')).unwrap();
    serde_json::from_str(line).unwrap()
}

fn response_message_id(body: &str) -> i64 {
    body.lines().find_map(|line| line.strip_prefix("id: ")).unwrap().parse().unwrap()
}

fn signed_uri(
    fixture: &Fixture,
    channel: &str,
    kind: &str,
    body: &str,
    reply_to: Option<i64>,
    nonce: &str,
) -> String {
    let signature = signed_auth::sign_write(
        &fixture.signing_private,
        &fixture.participant_id,
        channel,
        kind,
        body,
        reply_to,
        nonce,
    )
    .unwrap();
    let mut uri = format!(
        "/w/{}?scheme={}&sig={}&channel={}&kind={}&body={}&nonce={}",
        fixture.participant_id,
        signed_auth::SIGNATURE_SCHEME,
        signature,
        channel,
        kind,
        body,
        nonce,
    );
    if let Some(reply_to) = reply_to {
        uri.push_str(&format!("&reply_to={reply_to}"));
    }
    uri
}

#[tokio::test]
async fn navigation_read_preserves_authoritative_fields() {
    let fixture = fixture("single");
    let conn = db::connect(&fixture.db_path).unwrap();
    let row = db::append_message(&conn, &fixture.identity, "control-systems", "insight", "hello", None).unwrap();
    drop(conn);
    let response = get(&fixture.router, "/r/control-systems?after=0&limit=10").await;
    let (status, _, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    let message = message_json(&body);
    assert_eq!(message["id"].as_i64(), Some(row.id));
    assert_eq!(message["source"], "single");
    assert_eq!(message["instance"], "single-main");
}

#[tokio::test]
async fn signed_navigation_write_is_idempotent_and_raw_key_auth_is_retired() {
    let fixture = fixture("single");
    let uri = signed_uri(&fixture, "general", "insight", "hello", None, "nav-001");
    let response = get(&fixture.router, &uri).await;
    let (status, _, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("status: created"));
    let id = response_message_id(&body);
    let replay = get(&fixture.router, &uri).await;
    let (_, _, replay_body) = response_text(replay).await;
    assert!(replay_body.contains("status: existing"));
    assert_eq!(response_message_id(&replay_body), id);

    let tampered = uri.replace("body=hello", "body=tampered");
    assert_eq!(get(&fixture.router, &tampered).await.status(), StatusCode::UNAUTHORIZED);
    let legacy = format!(
        "/w/{}?key=retired&channel=general&body=x&nonce=legacy",
        fixture.participant_id
    );
    assert_eq!(get(&fixture.router, &legacy).await.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn signed_navigation_write_validates_reply_targets_and_nonce_conflicts() {
    let fixture = fixture("rotary");
    let conn = db::connect(&fixture.db_path).unwrap();
    let target_writer = Identity { source: "single".into(), instance: "single-main".into(), label: None };
    let target = db::append_message(&conn, &target_writer, "conversation-architecture", "idea", "target", None).unwrap();
    drop(conn);

    let reply = signed_uri(
        &fixture,
        "conversation-architecture",
        "insight",
        "reply",
        Some(target.id),
        "reply-001",
    );
    let response = get(&fixture.router, &reply).await;
    let (status, _, body) = response_text(response).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains(&format!("reply_to: {}", target.id)));

    let first = signed_uri(&fixture, "general", "message", "first", None, "shared-nonce");
    assert_eq!(get(&fixture.router, &first).await.status(), StatusCode::OK);
    let conflict = signed_uri(&fixture, "general", "message", "different", None, "shared-nonce");
    let (status, _, body) = response_text(get(&fixture.router, &conflict).await).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert!(body.contains("nonce_conflict"));

    let missing = signed_uri(
        &fixture,
        "conversation-architecture",
        "message",
        "missing",
        Some(999999),
        "missing-reply",
    );
    let (status, _, body) = response_text(get(&fixture.router, &missing).await).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains("reply_target_not_found"));
}

#[tokio::test]
async fn distinct_signed_participants_keep_provenance_separate() {
    let single = fixture("single");
    let conn = db::connect(&single.db_path).unwrap();
    identity::provision_web_participant_identity(&conn, "rotary-main", "rotary", Some("Rotary")).unwrap().unwrap();
    let (rotary_private, rotary_public) = signed_auth::generate_keypair();
    identity::set_web_participant_signing_key(&conn, "rotary-main", &rotary_public).unwrap();
    drop(conn);

    let single_uri = signed_uri(&single, "general", "message", "from-single", None, "single-001");
    assert_eq!(get(&single.router, &single_uri).await.status(), StatusCode::OK);

    let rotary_signature = signed_auth::sign_write(
        &rotary_private, "rotary-main", "general", "message", "from-rotary", None, "rotary-001"
    ).unwrap();
    let rotary_uri = format!(
        "/w/rotary-main?scheme={}&sig={}&channel=general&kind=message&body=from-rotary&nonce=rotary-001",
        signed_auth::SIGNATURE_SCHEME, rotary_signature
    );
    assert_eq!(get(&single.router, &rotary_uri).await.status(), StatusCode::OK);

    let conn = db::connect(&single.db_path).unwrap();
    let rows = db::list_messages_after(&conn, 0, Some("general"), 10).unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].instance, "single-main");
    assert_eq!(rows[1].instance, "rotary-main");
}
''')

# Remove helper files in the implementation commit so this migration workflow is one-shot.
(ROOT / ".github/scripts/implement_totp_human_auth.py").unlink(missing_ok=True)
(ROOT / ".github/workflows/implement-totp-human-auth.yml").unlink(missing_ok=True)

print("TOTP human auth migration applied")
