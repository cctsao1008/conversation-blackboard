# Contract Projections

Conversation Blackboard has one application/domain semantic kernel and multiple external projections.

## Authority order

1. Rust application semantics and persistence/execution/authorization invariants are authoritative.
2. MCP schemas are adapter projections over those semantics.
3. OpenAPI is the native HTTP projection.
4. UTCP is discovery/invocation metadata over the HTTP/native capability surface.

No external description becomes a second source of domain truth.

## Canonical shared shapes

`src/contract_schema.rs` owns reusable externally visible semantic shapes used directly by MCP and by parity tests for the HTTP/UTCP projections. It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, execution receipt, execution-audit integrity, authorization-policy integrity, and the authorization-policy snapshot records/envelope (`DurableGrantSnapshot`, `DelegatedGrantSnapshot`, `AuthorizationPolicySnapshot`, and `AuthorizationPolicyEnvelope`).

`intent_id` identifies a semantic operation across transports. `delivery_id` identifies one transport delivery and belongs to ingress provenance. Multiple delivery IDs may carry the same intent ID, so `delivery_id` is deliberately absent from the semantic execution receipt.

Authorization-policy integrity has one canonical Rust report source: `authorization::audit_authorization_integrity(conn)`. REST, MCP, OpenAPI, and UTCP only project that report; they do not reimplement grant-integrity SQL or define additional violation semantics. Remote visibility is guarded by the dedicated `read_authorization_policy_integrity` capability against the global `authorization-policy-integrity` resource. An authorized invalid policy state is successful report data (`valid: false`), while an old authorization schema fails explicitly without migration or repair.


Authorization-policy inventory has one canonical Rust data source: `authorization::read_authorization_policy_snapshot(conn)`. REST `GET /api/authorization-policy`, MCP `blackboard_authorization_policy`, OpenAPI, and UTCP are projections only. The adapters do not enumerate `principal_grants` or `delegated_grants` themselves. Visibility is guarded independently by `read_authorization_policy` on the global `authorization-policy` resource, so inventory access cannot be inferred from integrity, execution-audit, or administration authority.

For MCP, authentication metadata may change the transport schema without changing the semantic tool. Modern Streamable HTTP with configured OIDC may omit body HMAC `auth` because the HTTP Bearer header authenticates the transport principal; legacy HTTP and stdio retain the participant-HMAC `auth` requirement. Both forms reach the same Blackboard authorization kernel and the same canonical snapshot reader.

## Generation and validation rule

> Generate first. Validate second. Never maintain identical semantics manually in multiple contracts.

Where runtime adapter schemas can directly reuse Rust schema builders, they do. Where an external file format must remain a checked-in projection, `contract_parity_tests` structurally validates the duplicated shape in normal Rust CI. Contract drift therefore fails the same Linux/Windows validation path as implementation drift.

## Verification boundary

Normal `core-ci` is the acceptance boundary for contract projection changes. The Rust test suite parses checked-in OpenAPI and UTCP projections, compares shared semantic shapes against `src/contract_schema.rs`, and verifies that transport delivery identity does not leak into semantic execution receipts. Linux and Windows validation must both remain green before the projection contract is considered complete.

`utcp-contract` adds an independent discovery/invocation smoke gate for changes to the HTTP/UTCP projection surface. Policy-integrity projection changes are complete only when the canonical Rust parity tests, normal cross-platform core CI, and the UTCP discovery/invocation gate remain green.