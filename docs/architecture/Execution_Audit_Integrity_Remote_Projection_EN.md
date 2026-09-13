# Execution Audit Integrity Remote Projection

## Purpose

This document defines the remote projection boundary introduced by GitHub issue #86.

Issue #85 established the canonical read-only execution-audit integrity verifier. Issue #86 exposes that verifier through supported remote adapters without changing its semantics.

Core rule:

> Current authorization decides who may inspect integrity now. Persisted execution evidence determines what the integrity report says.

Remote adapters must not duplicate integrity SQL, reinterpret historical authorization, consume delegated authority, or repair history.

## Canonical Flow

```text
current reader Principal
        |
        v
READ_EXECUTION_AUDIT authorization
(resource = intent_id)
        |
        v
verify_execution_audit_integrity()
        |
        v
ExecutionAuditIntegrityReport
        |
        +--> REST
        |
        +--> MCP
        |
        +--> OpenAPI / UTCP metadata projections
```

The authorization check protects access to the report. It is not part of the historical integrity calculation.

## Authorization Boundary

Remote integrity reads reuse the existing `read_execution_audit` capability.

This is deliberate. The integrity report is a derived read model over the same protected execution-audit domain as the immutable audit bundle. Creating a second integrity-specific read capability would create overlapping policy semantics with little benefit.

The target intent identifier is the authorization resource. Therefore an explicit grant scoped to one intent must not authorize integrity inspection of a different intent.

```text
read_execution_audit(intent-A)
    intent-A integrity -> allowed
    intent-B integrity -> denied
```

Existing explicit-grant precedence remains authoritative. Broader implicit compatibility authority must not bypass an explicit resource restriction.

## REST Projection

The REST projection is:

```text
GET /api/executions/{intent_id}/audit/integrity
```

The handler:

1. resolves the current authenticated identity and principal;
2. normalizes `intent_id`;
3. authorizes `READ_EXECUTION_AUDIT` for the participant and intent resource;
4. calls `verify_execution_audit_integrity(...)`;
5. returns the resulting canonical report.

It does not reproduce integrity checks in the HTTP layer.

## MCP Projection

The MCP projection is the read-only tool:

```text
blackboard_execution_audit_integrity
```

Its inputs are:

- `participant_id`
- `intent_id`
- participant HMAC authentication proof

The tool reuses the same `READ_EXECUTION_AUDIT` capability and the same canonical verifier used by REST and the local CLI.

Tool annotations remain read-only, non-destructive, and idempotent.

## Canonical Report

The remote schema projects the Rust `ExecutionAuditIntegrityReport` shape:

```text
participant_id
intent_id
valid
checks[]
violations[]
```

`valid` is derived from the structural integrity verifier. `checks[]` records satisfied invariants and `violations[]` records detected structural failures.

The adapter does not invent additional integrity semantics.

## Contract Projection Rule

Rust application semantics remain authoritative.

```text
Rust verifier / canonical schema
        |
        +--> REST implementation
        +--> MCP implementation
        +--> OpenAPI
        +--> UTCP
```

OpenAPI, MCP schemas, UTCP, and adapter profiles are projections. They must track the canonical Rust shape rather than become independent domain definitions.

## Historical Semantics

The integrity verifier reads persisted evidence. It must not call current authorization logic to decide whether the original execution was historically legitimate.

These are separate questions:

```text
Can this caller inspect the report now?
    -> current authorization

Is the committed historical evidence structurally self-consistent?
    -> canonical integrity verifier
```

Changing grants later can change access to the report, but it must not change the report produced from the same persisted evidence.

## Read-Only Guarantee

Remote integrity verification must not:

- consume delegated grants;
- mutate receipts;
- mutate authorization provenance;
- mutate ingress provenance;
- repair missing rows;
- delete inconsistent history;
- synthesize historical authorization from current policy.

Integrity diagnosis and repair remain separate concerns.

## Security Boundary

The integrity report exposes only non-secret structural evidence.

It must not expose:

- JWTs;
- bearer tokens;
- HMAC secrets;
- TOTP secrets or codes;
- session secrets;
- raw credentials.

Authentication protects access; credentials are never part of the historical report.

## Architectural Position

With issues #82 through #86, the execution-audit path is now layered as follows:

```text
#82  persist historical authorization provenance
#83  assemble immutable local execution audit bundle
#84  expose authorized remote audit bundle
#85  verify structural integrity of committed audit evidence
#86  expose authorized remote integrity verification
```

The resulting distinction is intentional:

```text
audit read       -> what committed history contains
integrity verify -> whether that history is structurally self-consistent
current policy   -> who may inspect either surface now
```

## Design Principle

> Authenticate the reader. Authorize access to the audit domain. Verify persisted evidence with one canonical integrity kernel. Project the result without rewriting history.
