# Adapter Capability Profiles

Conversation Blackboard keeps domain semantics below transport adapters. REST, MCP, GitHub mailbox, CLI, UTCP descriptions, and future transports are projections over the same execution and authorization kernel; none defines domain truth.

## Profiles

| Adapter | Transport | Read messages | Post message | Access context | Execution receipt | Administration | Delivery model |
| --- | --- | --- | --- | --- | --- | --- | --- |
| REST | native HTTP | yes | yes | yes | yes | human-admin routes | synchronous |
| MCP | Streamable HTTP / JSON-RPC tools | yes | yes | yes | yes | no | synchronous |
| GitHub mailbox | GitHub Issue webhook | no | yes | no | no | no | asynchronous durable courier |
| CLI | native process | client projection | client projection | indirect | indirect | participant / DB administration | local process |

These differences are intentional capability profiles, not semantic drift.

## Shared invariants

All message-writing adapters that reach the semantic execution kernel preserve the same invariants: `participant_id` remains durable attribution identity; authenticated actors normalize to a `Principal`; semantic intent is independent of delivery identity; authorization uses the common scoped policy kernel; semantic idempotency is intent-bound; and accepted execution atomically commits effect, ingress provenance, and receipt.

## REST

REST is the native HTTP projection. It exposes message read/write, `/api/access-context`, `/api/executions/{intent_id}`, and human administration where authorized. REST schemas are HTTP contracts, not canonical domain definitions.

## MCP

MCP is the first-class agent adapter. Its tool surface includes `blackboard_read`, `blackboard_write`, `blackboard_access_context`, and `blackboard_execution_receipt`. Access context projects effective grants from the shared authorization kernel. Execution receipt reads the shared durable receipt produced by semantic execution. HMAC proofs authenticate participant calls and are bound to the canonical capability request.

## GitHub mailbox

GitHub mailbox is intentionally narrower: an asynchronous durable courier for message intent. GitHub webhook authentication establishes a principal; the same execution/authorization kernel decides whether the requested participant/resource/capability is allowed. Synchronous access-context and receipt-query parity are intentionally not required.

## CLI and UTCP

The CLI is an operational/client projection rather than a competing domain API. UTCP is discovery/invocation metadata and remains downstream of application semantics.

## Boundary with #76

This document defines which capabilities each adapter intentionally exposes. Schema generation and automated parity across OpenAPI, MCP schemas, and UTCP belong to #76.
