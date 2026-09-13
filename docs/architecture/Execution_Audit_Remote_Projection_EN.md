# Execution Audit Remote Projection

## Purpose

Conversation Blackboard exposes immutable committed execution evidence to authorized remote readers without re-evaluating the historical authorization decision that originally admitted the execution.

The architectural boundary is:

```text
current reader Principal
        ↓
current authorization policy
        ↓
READ_EXECUTION_AUDIT on participant + intent
        ↓
get_execution_audit_bundle()
        ↓
committed historical evidence only
        ↓
REST / MCP / UTCP projections
```

Current policy decides **who may inspect history now**. Persisted evidence decides **what that history says**.

## Capability

Remote audit access uses a distinct capability:

```text
read_execution_audit
```

This intentionally remains separate from `read_execution_receipt` so access to a semantic receipt does not automatically imply access to the complete historical audit bundle.

Explicit scoped grants continue to suppress broader implicit compatibility authority for the same capability. For example:

```text
principal  = participant-hmac:maker-main
capability = read_execution_audit
resource   = intent-A
```

allows audit access to `intent-A` but not `intent-B`.

## Canonical Read Model

The shared historical read model is:

```text
ExecutionAuditBundle
├── receipt
├── authorization
└── ingress[]
```

It is assembled only from committed durable state:

```text
execution_receipts
execution_authorization_provenance
ingress_provenance
```

The read model does not call `evaluate_authorization()` to reconstruct historical authorization truth.

## REST Projection

The REST surface is:

```text
GET /api/executions/{intent_id}/audit
```

The authenticated request first resolves the current principal and authorizes `read_execution_audit` for the current participant and target intent. If allowed, the handler returns the shared `ExecutionAuditBundle`.

The route is owned by the access API router and therefore participates in the production runtime through router composition:

```text
http::app(...)
    .merge(history::app(...))
    .merge(access_api::app(...))
    .merge(mcp::app(...))
    .merge(github_webhook::app(...))
```

This composition is important for contract tests: a test that exercises `/api/executions/...` must include `access_api::app(...)`, not only the base HTTP router.

## MCP Projection

The MCP read-only tool is:

```text
blackboard_execution_audit
```

It accepts the same semantic identifiers used by the execution audit model and returns the same historical bundle shape. The tool remains read-only and idempotent.

## Contract Projection

The canonical Rust schema defines the audit object and related ingress and authorization provenance structures. OpenAPI, MCP, and UTCP project that same semantic shape.

The contract hierarchy remains:

```text
Rust application semantics
          │
          ├── OpenAPI
          ├── MCP
          └── UTCP
```

No transport contract becomes an independent source of domain truth.

## Historical Semantics

Audit reads preserve the separation between current access policy and historical execution evidence:

```text
current policy
    └── may allow or deny the reader now

historical audit bundle
    └── records the committed execution as it happened then
```

A later grant deactivation, participant-policy change, or resource-scope change can affect whether a caller may read the audit now, but it must not rewrite the persisted authorization source, reason, grant reference, receipt, or ingress provenance.

## Security Boundary

Remote audit projections never expose raw credentials. In particular, audit responses do not contain:

```text
JWT
bearer token
HMAC secret
TOTP value
session secret
raw authentication material
```

Only non-secret principal identity and historical semantic/provenance metadata are returned.

## Issue Lineage

This projection builds on the following architecture sequence:

```text
#81  canonical AuthorizationDecision
#82  persisted historical authorization provenance
#83  immutable local ExecutionAuditBundle + CLI
#84  authorized REST / MCP / contract projections
```

The invariant introduced by #84 is:

> Current authorization protects access to history; it does not reinterpret history.
