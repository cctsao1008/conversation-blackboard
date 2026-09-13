# Contract Projections

Conversation Blackboard has one application/domain semantic kernel and multiple external projections.

## Authority order

1. Rust application semantics and persistence/execution/authorization invariants are authoritative.
2. MCP schemas are adapter projections over those semantics.
3. OpenAPI is the native HTTP projection.
4. UTCP is discovery/invocation metadata over the HTTP/native capability surface.

No external description becomes a second source of domain truth.

## Canonical shared shapes

`src/contract_schema.rs` owns reusable externally visible semantic shapes used directly by MCP and by parity tests for the HTTP/UTCP projections. It covers `participant_id`, `conversation_ref`, `intent_id`, `delivery_id`, message shape, access context, and execution receipt.

`intent_id` identifies a semantic operation across transports. `delivery_id` identifies one transport delivery and belongs to ingress provenance. Multiple delivery IDs may carry the same intent ID, so `delivery_id` is deliberately absent from the semantic execution receipt.

## Generation and validation rule

> Generate first. Validate second. Never maintain identical semantics manually in multiple contracts.

Where runtime adapter schemas can directly reuse Rust schema builders, they do. Where an external file format must remain a checked-in projection, `contract_parity_tests` structurally validates the duplicated shape in normal Rust CI. Contract drift therefore fails the same Linux/Windows validation path as implementation drift.
