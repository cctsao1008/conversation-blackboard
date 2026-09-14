# Database-Wide Execution Audit Sweep

## Purpose

Conversation Blackboard already has a canonical per-execution integrity verifier keyed by the semantic execution identity:

```text
(participant_id, intent_id)
```

That verifier answers a focused question:

> Given one semantic execution identity, is the persisted historical execution evidence internally consistent?

Issue #97 adds the complementary database-wide question:

> Across the current execution corpus, which receipt-backed executions are invalid, and which durable evidence rows are no longer attached to any committed execution identity?

The sweep is a **diagnostic read model**. It is not a repair mechanism, migration path, garbage collector, or authorization-policy reconstruction tool.

## Core invariant

```text
Database-wide audit scanning
    = discover + classify + verify
    != migrate + backfill + repair + delete
```

The sweep preserves the forensic boundary established by the read-only audit work:

- no DDL;
- no DML;
- no schema migration;
- no backfill;
- no repair;
- no delegated-grant consumption;
- no current-policy authorization evaluation.

Historical evidence is inspected as persisted.

## Canonical model

The Rust application layer defines the sweep result:

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

`ExecutionAuditIntegrityReport` remains the canonical result for one receipt-backed semantic execution. The database-wide sweep does not reproduce those rules.

## Two-stage verification

The sweep has two responsibilities.

### 1. Verify every receipt-backed execution

The sweep enumerates semantic execution identities from `execution_receipts`:

```text
execution_receipts
    ↓
(participant_id, intent_id)
    ↓
verify_execution_audit_integrity(...)
```

Every invalid per-execution result is preserved in `invalid_executions` with the existing canonical diagnostic strings.

This delegation is important. The database-wide scanner does not create a second interpretation of message/hash/navigation/authorization/ingress/delegated-grant integrity.

### 2. Discover evidence that has no receipt-backed identity

A per-execution verifier cannot discover evidence if the operator does not already know which semantic identity to inspect. The sweep therefore searches for durable rows that cannot be attached to a matching receipt.

The canonical orphan classes introduced by #97 are:

```text
orphan_authorization_provenance
orphan_ingress_provenance
orphan_navigation_reservation
unbound_legacy_ingress
```

These findings are returned separately from `invalid_executions` because an orphan row is not itself a receipt-backed execution.

## Orphan semantics

### Authorization provenance

An authorization-provenance row is orphaned when its exact semantic identity has no matching execution receipt:

```text
execution_authorization_provenance(participant_id, intent_id)
        without
execution_receipts(participant_id, intent_id)
```

Diagnostic:

```text
orphan_authorization_provenance
```

The sweep does not call the authorization kernel to decide whether that historical operation would be allowed today.

### Participant-bound ingress provenance

Ingress provenance is attached to semantic execution identity by `(participant_id, intent_id)`. A participant-bound ingress row with no matching receipt is reported as:

```text
orphan_ingress_provenance
```

`delivery_id` remains transport provenance only. It does not become semantic identity during the sweep.

### Unresolved legacy ingress

Legacy ingress can contain a missing participant binding. Such a row cannot be safely assigned to a participant from `intent_id` alone because multiple participants may legitimately reuse the same `intent_id`.

Therefore:

```text
participant_id = NULL / empty
    ↓
unbound_legacy_ingress
```

The sweep deliberately does **not** guess or backfill participant attribution.

### Navigation / idempotency reservation

A navigation reservation is orphaned when:

```text
navigation_writes(instance, nonce)
        has no matching
execution_receipts(participant_id, intent_id)
```

where the semantic correspondence is:

```text
instance       ↔ participant_id
nonce          ↔ intent_id
request_hash   ↔ receipt.intent_hash
message_id     ↔ receipt.message_id
```

Diagnostic:

```text
orphan_navigation_reservation
```

The existing per-execution verifier remains responsible for validating hash/message binding when a matching receipt exists.

## Identity discipline

The sweep preserves the same identity model as execution itself:

```text
semantic execution identity = (participant_id, intent_id)
```

Consequences:

- two participants may use the same `intent_id` without collision;
- ingress for one participant must not satisfy another participant's audit;
- `delivery_id` is not substituted for semantic identity;
- unresolved legacy ingress cannot be attributed from `intent_id` alone.

Regression coverage explicitly exercises two participants sharing one intent identifier.

## Deterministic output

Receipt identities are scanned in `(participant_id, intent_id)` order. Orphan evidence is sorted by:

```text
kind
→ participant_id
→ intent_id
→ reference
```

Deterministic ordering makes CLI output and future projections stable enough for operators and automated diagnostics without turning the report into a persisted event stream.

## Operator CLI

The sweep is exposed through the existing execution command family:

```text
conversation-blackboard execution verify-all --db board.db
```

The command:

1. requires the database file to exist;
2. opens SQLite read-only;
3. requires the current execution schema;
4. runs `sweep_execution_audit_integrity()`;
5. prints corpus summary and detailed invalid/orphan findings;
6. succeeds only when the sweep report is valid.

Example summary shape:

```text
EXECUTION AUDIT SWEEP
valid              : true
executions_scanned : 12
invalid_executions : 0
orphan_evidence    : 0
```

If the execution schema is stale, the command reports that explicit migration is required. It does not migrate while auditing.

## Read-only guarantee

Both the canonical sweep and CLI are observational.

The CLI uses a read-only SQLite connection, and regression tests verify that:

- a clean current-schema database succeeds;
- invalid sweep state fails;
- an unmigrated schema is refused;
- refusing an unmigrated database does not create execution tables;
- the canonical sweep does not change durable rows while detecting corruption or orphan evidence.

Migration remains an explicit operational action outside the audit path.

## Relationship to the integrity sequence

The execution-audit line now separates three concerns:

```text
Per-execution evidence read
        ↓
Per-execution integrity verification
        ↓
Database-wide discovery and integrity sweep
```

Relevant progression:

```text
#82  atomic authorization evidence
#83  canonical execution audit read model
#84  authorized remote audit projection
#85  canonical per-execution integrity verification
#86  authorized remote integrity projection
#87  ingress bound to semantic execution identity
#90  receipt hash bound to persisted semantic effect
#93  historical authorization context strengthened
#95  migration separated from forensic reads
#96  complete atomic-boundary integrity coverage
#97  database-wide integrity discovery and orphan detection
```

## Projection boundary

Issue #97 intentionally adds only the canonical Rust model and operator CLI.

REST, MCP, OpenAPI, and UTCP remain backward-compatible. If a remote sweep projection is introduced later, it should project `ExecutionAuditSweepReport` rather than issue independent adapter-local SQL or invent a second integrity policy.

## Non-goals

This work does not introduce:

- automatic repair or deletion;
- background garbage collection;
- event sourcing;
- cryptographic signatures, hash chains, or Merkle trees;
- a new authorization policy;
- a new semantic identity model;
- remote sweep endpoints.

The sweep answers one operational question only: **what persisted execution evidence is currently inconsistent or unattached, without changing it?**
