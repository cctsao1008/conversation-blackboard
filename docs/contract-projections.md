# Contract Projections

Conversation Blackboard has one application/domain semantic kernel and multiple external projections.

## Authority order

1. Rust application semantics and persistence/execution/authorization invariants are authoritative.
2. MCP schemas are adapter projections over those semantics.
3. OpenAPI is the native HTTP projection.
4. UTCP is discovery/invocation metadata over the HTTP/native capability surface.

No external description becomes a second source of domain truth.

## Canonical shared shapes

`src/contract_schema.rs` owns reusable externally visible semantic shapes used directly by MCP and by parity tests for the HTTP/UTCP projections. It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, execution receipt, execution-audit integrity, authorization-policy integrity, bounded authorization-policy grant-window records/envelope, authorization-decision explanation/envelope, and authorization-administration event/history/envelope shapes.

`intent_id` identifies a semantic operation across transports. `delivery_id` identifies one transport delivery and belongs to ingress provenance. Multiple delivery IDs may carry the same intent ID, so `delivery_id` is deliberately absent from the semantic execution receipt.

Authorization-policy integrity has one canonical Rust report source: `authorization::audit_authorization_integrity(conn)`. REST, MCP, OpenAPI, and UTCP only project that report; they do not reimplement grant-integrity SQL or define additional violation semantics. Remote visibility is guarded by the dedicated `read_authorization_policy_integrity` capability against the global `authorization-policy-integrity` resource. An authorized invalid policy state is successful report data (`valid: false`), while an old authorization schema fails explicitly without migration or repair.

Authorization-policy inventory is a privileged bounded selected-store read model. The canonical source is `authorization::read_authorization_policy_window(conn, ...)`. REST `GET /api/authorization-policy`, MCP `blackboard_authorization_policy`, OpenAPI, and UTCP are projections only; adapters do not enumerate `principal_grants` or `delegated_grants` themselves. Each request explicitly selects the independent `durable` or `delegated` grant ID domain and traverses it with keyset cursors rather than OFFSET or unbounded full-corpus materialization. Default order is `desc`, default limit is 20, maximum limit is 200, `before` is valid only for descending traversal, and `after` is valid only for ascending traversal. Exactly one grant array is populated for the selected store and the result carries `store`, `order`, and `has_more`. Inactive durable grants and expired/consumed/inactive delegated grants remain observable across pages. Visibility is still guarded independently by `read_authorization_policy` on the global `authorization-policy` resource, so pagination or store selection changes observation only and never grants or narrows reader authority.

For MCP, authentication metadata may change the transport schema without changing the semantic tool. Modern Streamable HTTP with configured OIDC may omit body HMAC `auth` because the HTTP Bearer header authenticates the transport principal; legacy HTTP and stdio retain the participant-HMAC `auth` requirement. Both forms reach the same Blackboard authorization kernel and the same canonical bounded policy-window reader.

Authorization decision explanation has one canonical read-only evaluator: `authorization::explain_authorization(conn, ...)`, returning the same decision semantics used by execution without schema migration or delegated-grant consumption. REST `GET /api/authorization-decision/explain`, MCP `blackboard_authorization_decision`, OpenAPI, and UTCP project that result. Caller authority is separate from the target Principal being evaluated. REST participant-HMAC binds the target context through the full query-bearing request target; MCP participant-HMAC uses a decision-specific canonical proof binding the complete normalized target context. OIDC/Bearer MCP callers remain authenticated at the HTTP transport boundary. Adapters never classify policy reasons or enumerate grant tables for explanation.

Authorization administration mutation is intentionally narrower than the read-side projection set. Phase-1 mutation is REST-only and is documented in OpenAPI for durable create/reactivation/deactivation and delegated create/deactivation. Those HTTP operations are thin projections over authorized `authorization_admin` entry points; OpenAPI advertises Bearer and Human Web session authentication, while participant-HMAC JSON mutation is unsupported in Phase 1. UTCP/MCP grant-mutation tools are not introduced by this phase. Parity tests therefore verify the REST mutation operations and schemas while also guarding against accidental MCP mutation projection.

Authorization-administration history is a separate privileged bounded read model. The canonical source is `authorization_admin::read_administration_event_window(conn, ...)`; REST, MCP, OpenAPI, and UTCP only project its immutable non-secret event/history window contract. Remote reads use event-id keyset cursors rather than OFFSET or unbounded table materialization: default order is `desc`, default limit is 20, maximum limit is 200, `before` is valid only for descending traversal, and `after` is valid only for ascending traversal. The result carries `events`, `order`, and `has_more`. Visibility is guarded by `read_authorization_administration_history` on `authorization-administration-history`, independently from `manage_authorization_policy`. Optional participant filtering affects returned history only and never narrows or grants reader authority. Adapter-source parity guards prohibit direct `authorization_admin_events` enumeration SQL and prohibit retention of the retired unbounded remote reader; stale schema fails without migration or repair.

## Generation and validation rule

> Generate first. Validate second. Never maintain identical semantics manually in multiple contracts.

Where runtime adapter schemas can directly reuse Rust schema builders, they do. Where an external file format must remain a checked-in projection, `contract_parity_tests` structurally validates the duplicated shape in normal Rust CI. Contract drift therefore fails the same Linux/Windows validation path as implementation drift.

## Verification boundary

Normal `core-ci` is the acceptance boundary for contract projection changes. The Rust test suite parses checked-in OpenAPI and UTCP projections, compares shared semantic shapes against `src/contract_schema.rs`, and verifies that transport delivery identity does not leak into semantic execution receipts. Linux and Windows validation must both remain green before the projection contract is considered complete.

`utcp-contract` adds an independent discovery/invocation smoke gate for changes to the HTTP/UTCP projection surface. Authorization-policy integrity, bounded authorization-policy inventory, authorization-decision explanation, and authorization-administration history projection changes are complete only when canonical Rust parity tests, normal cross-platform core CI, and the UTCP discovery/invocation gate all remain green at the same final repository HEAD.

For #109 specifically, closure evidence is deliberately same-head: the final clean repository HEAD must simultaneously pass Linux and Windows `core-ci` plus `utcp-contract`. Migration-helper test runs are implementation evidence but are not substitutes for this final projection acceptance boundary.

For #111 specifically, the acceptance freeze requires the same final clean repository HEAD to prove the bounded authorization-administration history contract across canonical Rust semantics, REST/MCP projection parity, Linux and Windows `core-ci`, and the `utcp-contract` discovery/invocation gate. Migration workflow success is implementation evidence; it is not a substitute for this same-head closure boundary.

For #112 specifically, the acceptance freeze requires one exact final clean repository HEAD to prove selected-store bounded authorization-policy traversal across canonical Rust semantics, REST/MCP/OpenAPI/UTCP projection parity, Linux and Windows `core-ci`, and the `utcp-contract` discovery/invocation gate. Migration workflow success is implementation evidence only; it does not replace this same-head closure boundary.
