# Execution Audit: Atomic-Boundary Integrity

## Purpose

Execution audit integrity is intended to answer a historical question:

> Does the durable evidence for this committed semantic execution still agree with the atomic execution boundary that originally committed it?

Issue #96 extends the canonical integrity verifier beyond receipt, message effect, authorization provenance, and ingress provenance. It now also verifies the durable state required for semantic replay safety and delegated one-shot correctness.

The verified execution boundary is:

```text
message effect
+ navigation/idempotency reservation
+ ingress provenance
+ execution receipt
+ authorization decision snapshot
+ delegated one-shot consumption, when applicable
```

This remains a read-only historical verification operation. It does not repair, reconstruct, migrate, or re-authorize the execution.

---

## Semantic identity and replay reservation

A committed message execution is identified semantically by:

```text
(participant_id, intent_id)
```

The durable idempotency reservation in `navigation_writes` must agree with the execution receipt:

```text
navigation_writes.instance      == receipt.participant_id
navigation_writes.nonce         == receipt.intent_id
navigation_writes.request_hash  == receipt.intent_hash
navigation_writes.message_id    == receipt.message_id
```

These checks connect the semantic intent identity, the canonical request hash, and the committed message effect.

A valid reservation produces checks such as:

```text
navigation_reservation_present
navigation_binding_valid
navigation_hash_matches_receipt
```

Missing or inconsistent evidence produces diagnostics such as:

```text
navigation_reservation_missing
navigation_binding_mismatch
navigation_hash_mismatch
```

### Diagnostic fallback lookup

If the exact `(participant_id, intent_id)` reservation is not found, the verifier may inspect a row sharing the intent nonce or committed message id. This fallback exists only to distinguish a corrupted binding from a genuinely missing reservation.

It does **not** rebind, recreate, or otherwise repair the reservation.

---

## Delegated one-shot consumption

Historical authorization provenance is authoritative for why an execution was accepted at commit time.

When the historical authorization snapshot records:

```text
authorization.source == "delegated_grants"
authorization.grant_id == Some(id)
```

the verifier inspects that referenced delegated grant as historical execution evidence.

The stable binding checks are:

```text
delegated_grants.id             == authorization.grant_id
delegated_grants.participant_id == receipt.participant_id
delegated_grants.capability     == receipt.capability
```

For a one-shot grant, the committed execution additionally requires:

```text
delegated_grants.one_shot           == 1
delegated_grants.consumed_at         IS NOT NULL
delegated_grants.consumed_intent_id  == receipt.intent_id
```

Successful checks include:

```text
delegated_grant_present
delegated_grant_binding_valid
delegated_one_shot_consumption_valid
```

Violations distinguish missing reference/evidence, binding corruption, and consumption corruption:

```text
delegated_grant_reference_missing
delegated_grant_missing
delegated_grant_binding_mismatch
delegated_one_shot_consumption_missing
delegated_one_shot_consumption_mismatch
```

A delegated grant that is not one-shot is not required to have consumption state. Its stable binding can still be checked and is reported with:

```text
delegated_grant_non_one_shot
```

---

## Historical evidence is not current policy

Integrity verification does not call the current authorization evaluator.

The distinction is deliberate:

```text
historical delegated-consumption evidence
        !=
current delegated-grant policy
```

A grant can be inactive or expired today without invalidating a past execution that was correctly authorized and atomically committed at the time.

Therefore current fields such as grant status and current expiry do not determine historical execution validity.

Likewise, the verifier must not reconstruct missing historical principal or resource values from current policy.

---

## Read-only boundary

Issue #95 established the stronger operational rule:

> Reading or verifying committed execution evidence must not change the evidence, schema, or database operating mode being inspected.

Issue #96 preserves that rule.

The new navigation and delegated-grant checks perform observation only. They do not perform:

```text
DDL
DML
schema migration
historical backfill
repair
re-authorization
```

A missing or inconsistent row becomes a diagnostic result rather than an opportunity for the verifier to modify the database.

---

## Projection behavior

The canonical report shape remains unchanged:

```rust
ExecutionAuditIntegrityReport {
    participant_id,
    intent_id,
    valid,
    checks,
    violations,
}
```

The new integrity diagnostics are therefore inherited automatically by REST and MCP projections.

Adapters remain thin:

```text
REST / MCP
    ↓
canonical authorization for audit read
    ↓
verify_execution_audit_integrity(...)
    ↓
ExecutionAuditIntegrityReport
```

No adapter-local SQL or adapter-specific integrity semantics are introduced.

Because the report property set is unchanged, OpenAPI and MCP schema shapes do not need new top-level properties.

---

## Integrity coverage after #96

For a committed semantic execution, the verifier now covers the durable components that establish the execution's historical effect and replay safety:

```text
Execution receipt
    ↕ participant_id / intent_id / message_id / intent_hash / capability
Message effect
    ↕ semantic request hash + participant binding
Navigation reservation
    ↕ participant / intent / hash / message binding
Historical authorization snapshot
    ↕ principal / capability / resource / grant reference
Delegated one-shot state, when applicable
    ↕ participant / capability / consumed intent
Ingress provenance
    ↕ participant / intent / delivery metadata
```

The verifier remains intentionally per-execution. Database-wide orphan discovery or cryptographic evidence chaining are separate concerns.

---

## Evolution

The execution-audit integrity model has evolved incrementally:

```text
#82  persist authorization decision provenance atomically
#83  canonical audit read model
#85  structural execution-audit integrity verifier
#87  bind ingress provenance to participant + intent
#90  verify receipt hash against persisted semantic effect
#93  preserve historical authorization context
#95  separate migration from read-only audit/verification
#96  cover idempotency reservation and delegated one-shot consumption
```

The resulting principle is:

> A committed semantic execution is valid only when its durable effect, replay reservation, provenance, receipt, historical authorization evidence, and required one-shot consumption state remain mutually consistent — and verifying that consistency never changes the evidence being verified.
