# Runtime compatibility contract

The Rust migration replaces implementation, not product behavior. During migration, the Python runtime is the temporary behavior oracle and `compat/contract.json` is the machine-readable freeze.

## Frozen boundaries

The following remain compatible across Python and Rust:

```text
HTTP routes and status codes
JSON response shapes
request-size and page-size limits
message ordering and cursor semantics
channel filtering
reply_to behavior
server-resolved source / instance provenance
SHA-256 bearer-token lookup
SQLite messages / identities schema
WAL operation
static browser UI allow-list and security headers
```

The schema fingerprint is recorded in `compat/contract.json`. A change to `schema.sql` therefore fails the compatibility test unless the contract is deliberately versioned as a separate product change.

## Executable reference

`tools/compat_smoke.py` runs the same scenario against either runtime:

```powershell
py .\tools\compat_smoke.py --runtime python
cargo build
py .\tools\compat_smoke.py --runtime rust
```

The scenario checks authentication, a pre-existing SHA-256 token hash, registration, server-resolved identity, spoof rejection, UTF-8 posting, cursor reads, replies, channel summaries, invalid-query behavior, security headers, and restart persistence.

CI runs the scenario against both implementations during migration.

## Database continuity

The Rust runtime must open the existing `board.db` directly. There is no export/import or schema conversion step. Python and Rust must not write the production database concurrently during cutover.

## What is not frozen

Implementation details are intentionally free to change: threading/runtime model, Rust module structure, crate selection, internal error types, and code organization. Those details are not observable product contracts.
