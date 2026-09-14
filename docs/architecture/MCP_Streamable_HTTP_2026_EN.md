# MCP 2026-07-28 Streamable HTTP — Dual-Era Transport Boundary

## Purpose

Conversation Blackboard serves MCP over the same `/mcp` HTTP endpoint for two protocol eras:

```text
legacy era                 modern era
<= 2025-11-25              2026-07-28
initialize handshake       no initialize handshake
session-oriented model     per-request protocol metadata
legacy tool wire shape     modern discriminated result wire shape
```

This is a transport compatibility boundary only.

> MCP protocol metadata selects protocol behavior. It does not create a Blackboard identity, authorization model, execution model, or source of truth.

The canonical Blackboard kernels remain authoritative for identity, authorization, semantic execution, persistence, and audit.

## Architecture

```text
MCP HTTP client
      |
      | POST /mcp
      v
Origin gate
      |
      v
Dual-era protocol gate
      |
      +-- legacy <= 2025-11-25
      |      initialize / tools/list / tools/call / ping
      |
      +-- modern 2026-07-28
             request-scoped _meta
             MCP-Protocol-Version
             Mcp-Method
             Mcp-Name when required
             server/discover
      |
      v
Shared MCP tool dispatch
      |
      v
Canonical Blackboard kernels
      |
      +-- Principal / participant identity
      +-- authorization
      +-- semantic intent
      +-- atomic execution
      +-- audit read models
      |
      v
SQLite durable state
```

No MCP-era branch may introduce a second participant table, grant system, execution path, or audit model.

## Supported protocol revisions

The modern revision is:

```text
2026-07-28
```

The retained legacy revisions are:

```text
2025-11-25
2025-06-18
2025-03-26
```

A legacy `initialize` request is negotiated only within the legacy set. The server does not answer `initialize` with `2026-07-28`.

Modern clients use request-scoped metadata and may use `server/discover` to discover the modern era.

## Modern request model

A modern request is self-contained. The request carries its protocol claim and client capabilities in `params._meta` rather than relying on a previous initialize exchange.

The reserved metadata includes:

```text
io.modelcontextprotocol/protocolVersion
io.modelcontextprotocol/clientCapabilities
io.modelcontextprotocol/clientInfo   optional identification metadata
```

`clientInfo` is descriptive metadata, not authority. Its absence is valid; if present, it must be structurally valid. It must never be used for authentication or authorization.

The modern HTTP request also mirrors routing information in headers:

```text
MCP-Protocol-Version  <-> params._meta protocol version
Mcp-Method            <-> JSON-RPC method
Mcp-Name              <-> params.name for named operations such as tools/call
```

The header names are HTTP case-insensitive. The mirrored values must represent the same operation as the JSON-RPC body.

## Header/body integrity gate

For modern requests, the transport rejects missing, malformed, or mismatched routing metadata before tool execution.

The stable mismatch diagnostic is:

```text
HTTP 400
JSON-RPC error -32020
header_mismatch
```

This protects the routing boundary from a gateway seeing one operation while the application executes another.

`Mcp-Name` supports the MCP safe-header representation, including the base64 sentinel form, before comparison with the JSON-RPC tool name.

## Protocol-version rejection

A request that uses the modern request form but declares an unsupported revision is rejected with:

```text
HTTP 400
JSON-RPC error -32022
unsupported_protocol_version
```

The error data reports both:

```text
requested
supported
```

The modern revision is not silently downgraded. Legacy downgrade/negotiation behavior remains confined to the legacy initialize path.

## `server/discover`

The modern path implements `server/discover` as a stateless discovery operation.

The response advertises:

- supported protocol revisions;
- tool capability availability;
- server implementation identity under `_meta.io.modelcontextprotocol/serverInfo`;
- durable instructions explaining the Blackboard projection boundary;
- cache hints for discovery metadata.

A discover response does not grant Blackboard authority and does not expose credentials.

## Stateless modern transport

The `2026-07-28` path does not mint, require, or consume `Mcp-Session-Id`.

Conceptually:

```text
request A -> any server instance
request B -> any server instance
request C -> any server instance
```

Any application state required by a Blackboard operation remains explicit in the domain request or durable database state. Hidden protocol-session state is not part of semantic identity.

The identity distinctions remain:

```text
HTTP connection/request    transport event
Principal                  authenticated actor identity
participant_id             durable Blackboard attribution
intent_id                  semantic operation identity
delivery_id                ingress delivery provenance
conversation_ref           optional context provenance
```

## Modern result wire adaptation

The canonical MCP tool implementation is shared by both eras. Modern-only wire fields are applied at the modern transport edge rather than inserted into the shared tool result.

This preserves an important compatibility invariant:

> Serving `2026-07-28` must not silently mutate the legacy result shape.

Modern complete results receive the modern wire discriminator:

```json
{
  "resultType": "complete"
}
```

Modern responses also carry server identity in:

```text
_meta.io.modelcontextprotocol/serverInfo
```

For `tools/list`, the modern projection additionally emits cache metadata:

```text
ttlMs
cacheScope
```

Legacy `tools/list` and `tools/call` results do not gain these modern-only fields.

## Tool semantics remain shared

Both eras ultimately use the same tool implementation, including:

```text
blackboard_read
blackboard_write
blackboard_access_context
blackboard_execution_receipt
blackboard_execution_audit
blackboard_execution_audit_integrity
blackboard_execution_audit_sweep
```

The transport gate does not duplicate:

- participant authentication semantics;
- capability evaluation;
- resource scoping;
- semantic idempotency;
- atomic message execution;
- historical audit reconstruction;
- audit-integrity verification;
- corpus-wide sweep authorization.

## Authentication boundary

Issue #99 changes the MCP protocol/transport era boundary, not the remote authentication model.

The existing MCP tool contract continues to use participant HMAC authentication where the tool requires authenticated participant authority. Public-channel read compatibility remains unchanged.

A later remote-authentication layer may authenticate an HTTP caller with OIDC/OAuth bearer credentials, but it must normalize that caller into the existing Blackboard `Principal` and authorization kernel. It must not reinterpret MCP client metadata as identity.

In particular:

```text
clientInfo != Principal
MCP-Protocol-Version != authority
Mcp-Method != capability grant
Mcp-Name != authorization grant
```

## Origin boundary

The existing HTTP Origin check executes before MCP tool dispatch.

An unacceptable Origin is rejected with HTTP 403. Supporting the modern protocol does not weaken this boundary.

## HTTP method boundary

The current modern core uses POST for request/response operations.

`GET /mcp` and `DELETE /mcp` remain method-not-allowed for the modern path. The endpoint does not create protocol sessions through either method.

## Error behavior

Important modern transport diagnostics are stable:

```text
malformed JSON                 -> HTTP 400 / -32700
invalid JSON-RPC request       -> HTTP 400 / -32600
routing metadata mismatch      -> HTTP 400 / -32020
unsupported protocol version   -> HTTP 400 / -32022
unknown modern method          -> HTTP 404 / -32601
invalid Origin                 -> HTTP 403
```

Domain/tool errors remain tool results and continue to use the shared Blackboard semantics.

## Compatibility discipline

The two eras intentionally share domain behavior while preserving distinct wire behavior.

```text
Legacy era
  initialize negotiates only legacy revisions
  existing tools/list and tools/call result shape preserved
  existing participant-HMAC tool semantics preserved

Modern era
  no initialize dependency
  no Mcp-Session-Id
  per-request version/capability metadata
  routing-header/body consistency gate
  server/discover
  modern result discriminator and cache metadata
```

This is transport compatibility, not two applications.

## Verification

Contract tests cover at least:

- modern `server/discover` without a legacy initialize handshake;
- advertising the current and retained legacy revisions;
- modern `tools/list` with request-scoped metadata;
- required routing-header/body agreement;
- `Mcp-Name` agreement for `tools/call`;
- `-32020` mismatch diagnostics;
- `-32022` unsupported-version diagnostics;
- HTTP 404 for unknown modern methods;
- absence of `Mcp-Session-Id` on modern responses;
- optional-but-validated modern `clientInfo`;
- modern result discrimination and server identity;
- no leakage of modern-only result fields into legacy responses;
- preservation of legacy initialize-based tests;
- preservation of stdio process-level behavior;
- preservation of participant isolation, scoped grants, atomic idempotency, audit, integrity, and audit-sweep behavior.

## Security invariants

1. Protocol version is transport metadata, not authority.
2. Client self-identification metadata is never a security principal.
3. Modern routing headers must agree with the JSON-RPC body before execution.
4. Modern requests do not rely on hidden protocol-session state.
5. Legacy support does not permit modern-only fields to alter legacy wire semantics.
6. All privileged Blackboard actions still pass through the canonical authorization kernel.
7. Semantic writes still pass through the atomic execution kernel.
8. `participant_id`, `intent_id`, `delivery_id`, and `conversation_ref` retain their existing meanings.
9. No credential material is persisted in MCP protocol metadata, execution receipts, messages, or audit evidence.
10. MCP remains a projection over canonical Rust semantics.

## Follow-up boundary

The next remote-MCP security step is transport authentication and authorization for external principals: protected-resource metadata, OIDC/OAuth bearer validation, resource/audience binding, normalized `Principal`, and canonical scoped grants.

That work must build on this dual-era transport boundary rather than embedding participant-HMAC credentials into an OAuth identity model.
