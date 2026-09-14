# Execution Audit Sweep Remote Projection

## Purpose

Issue #98 projects the canonical database-wide execution audit sweep introduced by #97 through REST, MCP, and UTCP discovery without turning corpus-wide historical evidence into ordinary participant-scoped data.

The governing rule is:

> **Database-wide audit visibility is privileged. Remote adapters may expose the canonical sweep only after explicit sweep authorization; ordinary participant self-authority for per-execution audit does not imply corpus-wide visibility.**

This preserves the separation between application semantics and transport projections:

```text
Rust authorization + execution semantics
            ↓
read_execution_audit_sweep authorization
            ↓
sweep_execution_audit_integrity(...)
            ↓
REST / MCP / UTCP discovery projections
```

No adapter owns a second sweep implementation.

## Why the sweep needs a separate capability

Per-execution audit is scoped to one authenticated participant and one semantic execution identity:

```text
(participant_id, intent_id)
```

The database-wide sweep is different. It can report:

- invalid receipt-backed executions belonging to multiple participants;
- orphan authorization provenance;
- orphan participant-bound ingress provenance;
- orphan navigation reservations;
- unresolved legacy ingress whose participant binding cannot safely be inferred.

Therefore reusing `read_execution_audit` would be an authority escalation: an ordinary participant that may inspect its own execution history could gain visibility into evidence outside that participant scope.

#98 introduces a dedicated capability and resource:

```text
capability = read_execution_audit_sweep
resource   = execution-audit-sweep
```

These names are application-level authorization semantics, not transport-specific permissions.

## Authorization policy

The implicit compatibility policy for the sweep is deliberately narrower than ordinary audit reads.

| Principal class | Implicit sweep authority |
| --- | --- |
| `human-web` self, role `admin` | allowed |
| ordinary `human-web` self | denied |
| `participant-hmac` self | denied |
| GitHub owner compatibility principal | denied |
| bearer / external / OIDC principal | denied |

A durable explicit principal grant may authorize a principal deliberately:

```text
principal
  × participant_id
  × read_execution_audit_sweep
  × execution-audit-sweep
```

Explicit grants remain authoritative under the normal authorization kernel. The remote projections do not implement independent role checks or grant SQL.

For the implicit Human Web administrator path, the authorization decision reason is:

```text
implicit_human_web_admin_audit_sweep
```

The stable resource is required even for the implicit administrator rule so that effective grants and authorization explanations describe the same logical object.

## Canonical read model

The projected report remains the #97 application model:

```rust
pub struct ExecutionAuditOrphanEvidence {
    pub kind: String,
    pub participant_id: Option<String>,
    pub intent_id: String,
    pub reference: Option<String>,
}

pub struct ExecutionAuditSweepReport {
    pub valid: bool,
    pub executions_scanned: usize,
    pub invalid_executions: Vec<ExecutionAuditIntegrityReport>,
    pub orphan_evidence: Vec<ExecutionAuditOrphanEvidence>,
}
```

Remote adapters call:

```rust
execution::sweep_execution_audit_integrity(conn)
```

Receipt-backed entries continue to inherit the canonical per-execution verifier rather than duplicating integrity rules in an adapter.

## REST projection

The REST projection is:

```text
GET /api/execution-audit/sweep
```

The request path is:

```text
request authentication
    ↓
Principal derivation
    ↓
authorize(
    principal,
    participant_id,
    read_execution_audit_sweep,
    execution-audit-sweep,
)
    ↓ allowed
sweep_execution_audit_integrity(...)
    ↓
{"sweep": ExecutionAuditSweepReport}
```

Authentication and principal derivation reuse the existing request identity path. The handler performs no sweep SQL.

An invalid integrity report is still a successful authorized read:

```text
HTTP 200
{
  "sweep": {
    "valid": false,
    ...
  }
}
```

`valid: false` describes database evidence. It is not an HTTP transport failure.

## MCP projection

MCP exposes:

```text
blackboard_execution_audit_sweep
```

Input:

```text
participant_id
+ auth
```

No `intent_id` exists because the operation is corpus-wide.

The participant HMAC proof is bound to:

```text
read_execution_audit_sweep
+ execution-audit-sweep
```

A valid proof authenticates the request but does not itself authorize the sweep. The authorization kernel is evaluated separately, preserving the authentication/authorization boundary.

Both HTTP MCP and stdio MCP dispatch through the same tool implementation and return the same canonical sweep envelope.

The tool is advertised as read-only and idempotent.

## UTCP discovery projection

The project defines UTCP as discovery/invocation metadata over the native HTTP capability surface. Because #98 adds a new remote HTTP capability, the sweep is also discoverable as:

```text
execution_audit_sweep
```

with the HTTP target:

```text
${BLACKBOARD_URL}/api/execution-audit/sweep
```

UTCP does not grant authority. The Blackboard server still authenticates the bearer principal and evaluates `read_execution_audit_sweep` against `execution-audit-sweep` before the canonical sweep can execute.

The checked-in UTCP shape is parity-tested against the canonical Rust sweep/orphan schemas. This keeps UTCP as a projection rather than a second source of truth.

## Contract authority

The authority order remains:

```text
Rust application semantics
    ↓
canonical contract_schema builders
    ↓
MCP runtime schema
    ↓
OpenAPI HTTP projection
    ↓
UTCP discovery/invocation metadata
```

`src/contract_schema.rs` defines reusable sweep shapes for:

```text
ExecutionAuditOrphanEvidence
ExecutionAuditSweepReport
ExecutionAuditSweepEnvelope
```

Normal CI checks that OpenAPI, MCP, and UTCP projections stay aligned with those semantics.

## Read-only forensic boundary

#98 does not weaken the #95/#97 forensic rule:

```text
scan / read / verify
    !=
migrate / backfill / repair / delete
```

The remote sweep must not:

- migrate an old execution schema;
- backfill historical provenance;
- repair or delete orphan evidence;
- reconstruct missing evidence from current policy;
- re-evaluate a historical authorization decision as though it occurred now;
- consume delegated one-shot authority;
- store credentials in the report.

Current authorization is evaluated only to decide whether the caller may observe the sweep now. Historical execution validity remains determined from persisted historical evidence.

## Security boundary

The important distinction is:

```text
read_execution_audit
    = participant-scoped historical inspection

read_execution_audit_sweep
    = privileged corpus-wide forensic inspection
```

The second capability must never be inferred from the first.

This is especially important because `ExecutionAuditSweepReport` may reveal semantic execution identities and orphan evidence across participant boundaries even when no caller-supplied `intent_id` exists.

## Verification

Regression coverage introduced with #98 verifies at minimum:

- ordinary Human Web self cannot sweep;
- Human Web administrator self receives the narrow implicit sweep authority;
- participant-HMAC self cannot sweep by default;
- an explicit durable sweep grant can authorize participant-HMAC;
- GitHub owner compatibility authority does not imply sweep authority;
- REST returns `403` without sweep authority;
- authorized REST returns `200` for both clean and invalid reports;
- MCP advertises the privileged read-only sweep tool;
- MCP denies participant-HMAC without an explicit sweep grant;
- MCP succeeds with the explicit grant and projects canonical findings;
- stdio MCP exposes the same tool surface;
- OpenAPI, MCP, and UTCP remain aligned with canonical Rust sweep semantics.

## Relationship to previous audit work

```text
#82  persist authorization decision provenance atomically
  ↓
#83  canonical historical audit read model
  ↓
#84  authorized per-execution REST/MCP audit projection
  ↓
#85  canonical per-execution integrity verifier
  ↓
#86  remote integrity projection
  ↓
#87  bind ingress provenance to (participant_id, intent_id)
  ↓
#90  verify receipt hash against persisted semantic effect
  ↓
#95  separate migration from read-only verification
  ↓
#96  cover the full atomic execution evidence boundary
  ↓
#97  database-wide read-only integrity sweep
  ↓
#98  privileged remote projection of that sweep
```

The resulting architecture keeps one semantic kernel: transports authenticate and project; the authorization kernel decides current visibility; the execution/audit kernel defines historical evidence and integrity.
