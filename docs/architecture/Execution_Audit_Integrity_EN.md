# Execution Audit Integrity

## Purpose

Conversation Blackboard persists historical execution evidence across several durable records:

- `execution_receipts`
- `execution_authorization_provenance`
- `ingress_provenance`
- the semantic message effect referenced by `message_id`

Reading these records is not sufficient to prove that they are internally consistent. Direct SQLite edits, legacy rows, partial migrations, or database damage can leave individually readable records that no longer form a coherent committed execution.

Issue #85 therefore adds a read-only integrity verifier over committed execution evidence.

Core rule:

> Historical audit should not only be readable; its structural consistency should be verifiable without re-evaluating current authorization policy and without mutating history.

## Integrity Boundary

The verifier operates only on persisted historical evidence:

```text
(participant_id, intent_id)
        ↓
execution receipt
message effect
authorization provenance
ingress provenance
        ↓
structural checks
        ↓
ExecutionAuditIntegrityReport
```

It does not call the current authorization kernel to reinterpret why a historical execution was allowed.

It also does not repair, delete, or rewrite historical records.

## Canonical Report

The canonical result is represented conceptually as:

```text
ExecutionAuditIntegrityReport {
    participant_id
    intent_id
    valid
    checks[]
    violations[]
}
```

`valid` is true only when no structural violation is detected.

A valid committed execution includes the check marker:

```text
valid_committed_audit
```

## Verified Invariants

The verifier checks that:

1. A semantic execution receipt exists for the requested `(participant_id, intent_id)`.
2. The receipt is bound to the requested semantic identity.
3. The receipt status is `committed`.
4. The receipt's `message_id` refers to an existing semantic message effect.
5. Authorization provenance exists for the committed execution where the post-#82 model requires it.
6. Authorization provenance is bound to the same semantic execution identity.
7. Ingress provenance records, when present, remain bound to the same `intent_id`.
8. Ingress records contain non-empty delivery, transport, external-reference, and principal metadata.

Representative violation classes include:

```text
receipt_missing
receipt_binding_mismatch
message_effect_missing
authorization_provenance_missing
authorization_provenance_duplicate
authorization_binding_mismatch
invalid_receipt_status
ingress_binding_mismatch
ingress_metadata_invalid
```

## Current Policy Is Not Historical Truth

Integrity verification and authorization explanation answer different questions.

```text
grant explain
    -> Would this principal be allowed now?

execution audit
    -> What committed historical evidence exists?

execution verify-audit
    -> Is that committed historical evidence structurally coherent?
```

The integrity verifier must not reconstruct missing historical authorization provenance from today's grants or implicit authority.

A structurally incomplete historical record remains incomplete.

## Operator CLI

The local operator surface is:

```text
conversation-blackboard execution verify-audit \
  --db board.db \
  --participant-id maker-main \
  --intent-id intent-85
```

The command prints the integrity report and exits successfully only when the report is valid.

Example conceptual output:

```text
EXECUTION AUDIT INTEGRITY
participant_id : maker-main
intent_id      : intent-85
valid          : true
check          : receipt_present
check          : receipt_status_committed
check          : message_effect_present
check          : authorization_provenance_present
check          : ingress_binding_valid
check          : ingress_metadata_valid
check          : valid_committed_audit
```

## Read-Only Semantics

Verification is diagnostic only.

It must never:

- consume delegated grants,
- evaluate current policy to reconstruct historical authority,
- repair missing provenance,
- delete malformed rows,
- rewrite receipts,
- expose raw credential material.

This keeps the boundary clear:

```text
Historical evidence
        ↓
verify
        ↓
report
```

not:

```text
Historical evidence
        ↓
reinterpret / repair
        ↓
new history
```

## Relationship to Earlier Audit Work

```text
#82  Persist historical authorization provenance
        ↓
#83  Build immutable local execution audit bundle
        ↓
#84  Expose authorized REST / MCP audit projections
        ↓
#85  Verify structural integrity of committed audit evidence
```

#85 does not replace SQLite integrity checks and does not introduce cryptographic signatures or hash chaining. It verifies application-level semantic relationships across the committed execution evidence already owned by the Blackboard execution model.

## Architectural Principle

The audit subsystem now distinguishes three separate properties:

```text
readability
    Can the committed historical evidence be retrieved?

authorization
    May the current principal inspect that evidence?

integrity
    Does the retrieved historical evidence form a coherent committed execution?
```

These properties are intentionally separate.
