# Ingress Provenance Semantic Identity

## Purpose

Issue #87 closes a semantic-identity gap in execution ingress provenance.

The execution receipt and historical authorization provenance are already keyed by the semantic execution identity:

```text
(participant_id, intent_id)
```

Ingress provenance must obey the same binding rule even though `delivery_id` remains the transport-delivery identifier.

Core rule:

> A transport delivery may have its own `delivery_id`, but it belongs to exactly one semantic execution identity: `(participant_id, intent_id)`.

## Why This Matters

Before #87, `ingress_provenance` carried `intent_id` but no durable `participant_id` binding. Audit reads therefore selected deliveries by intent alone.

That was insufficient because `intent_id` is scoped by participant in the durable execution model. Two participants may legitimately use the same intent string:

```text
alpha-main / shared-intent
beta-main  / shared-intent
```

Without participant binding, transport deliveries for those two semantic executions could be mixed in one historical audit view.

## Canonical Identity Model

The execution identity model is now consistent across all committed evidence:

```text
ExecutionReceipt
    participant_id + intent_id

AuthorizationProvenance
    participant_id + intent_id

IngressProvenance
    participant_id + intent_id + delivery_id
```

The dimensions remain distinct:

```text
participant_id = durable Blackboard attribution identity
intent_id      = semantic operation identity within that participant
delivery_id    = concrete transport delivery identity
```

`delivery_id` is still not part of the semantic receipt.

## Durable Schema

`ingress_provenance` now includes a participant binding and an execution-oriented index:

```text
ingress_provenance
├── delivery_id
├── participant_id
├── intent_id
├── transport
├── external_ref
├── principal_provider
├── principal_subject
└── created_at
```

The execution lookup index is conceptually:

```text
(participant_id, intent_id)
```

The column remains nullable only to represent unresolved legacy rows during conservative migration.

## Atomic Write Rule

New ingress rows are written from the same semantic execution transaction that records the execution receipt.

The persisted participant binding comes from the receipt semantic identity, not from transport-local inference.

Conceptually:

```text
BEGIN TRANSACTION
    semantic effect
    execution receipt(participant_id, intent_id)
    ingress provenance(participant_id, intent_id, delivery_id)
    authorization provenance(participant_id, intent_id)
    optional delegated-grant consumption
COMMIT
```

A reused `delivery_id` must match the same participant, intent, transport, external reference, and principal metadata or the transaction is rejected.

## Audit Isolation

The canonical historical audit read now loads ingress provenance by both semantic identity components:

```sql
WHERE participant_id = ?
  AND intent_id = ?
```

Therefore these two executions remain isolated:

```text
alpha-main / shared-intent / delivery-alpha
beta-main  / shared-intent / delivery-beta
```

The alpha audit contains only `delivery-alpha`; the beta audit contains only `delivery-beta`.

REST and MCP inherit this behavior because they project the same canonical Rust audit read model rather than issuing adapter-specific provenance queries.

## Integrity Verification

The integrity verifier now treats participant binding as part of ingress structural integrity.

Relevant conditions include:

```text
ingress_binding_valid
ingress_binding_mismatch
ingress_participant_unbound
ingress_missing
ingress_metadata_invalid
```

A bound delivery contributes to a requested execution only when both participant and intent match.

An unresolved legacy delivery is not silently assigned to an arbitrary participant.

## Conservative Legacy Migration

Existing databases may contain ingress rows created before participant binding existed.

Migration follows a non-guessing rule.

### Unambiguous case

If an old ingress `intent_id` maps to exactly one distinct participant in `execution_receipts`, the row can be safely backfilled:

```text
legacy intent-X
    ↓
receipts show only maker-main / intent-X
    ↓
participant_id = maker-main
```

### Ambiguous case

If the same old `intent_id` maps to multiple participants, no participant is chosen:

```text
legacy shared-intent
    ├── alpha-main / shared-intent
    └── beta-main  / shared-intent

result:
participant_id remains unresolved
```

The integrity verifier reports `ingress_participant_unbound` rather than fabricating historical attribution.

This preserves historical uncertainty explicitly.

## Contract Projection

The public audit record schema now includes `participant_id` on each ingress audit record.

The canonical shape is:

```text
IngressAuditRecord
├── participant_id
├── delivery_id
├── intent_id
├── transport
├── external_ref
└── principal
```

OpenAPI and other external contracts remain projections of the Rust application semantics.

## Security Boundary

Participant binding adds semantic attribution, not credential storage.

Ingress audit evidence still excludes raw authentication material such as:

- bearer tokens;
- JWTs;
- HMAC secrets;
- TOTP values;
- session secrets.

The stored principal provider/subject pair remains non-secret identity metadata.

## Architectural Position

Issues #82 through #87 now establish the following execution-audit chain:

```text
#82  persist historical authorization provenance
#83  assemble immutable local execution audit
#84  expose authorized remote audit
#85  verify structural integrity
#86  expose remote integrity verification
#87  bind ingress provenance to the full semantic execution identity
```

The resulting invariant is:

> Receipt, authorization provenance, and ingress provenance all refer to the same semantic execution through `(participant_id, intent_id)`, while `delivery_id` remains transport provenance only.

## Design Principle

> Preserve transport identity without confusing it with semantic identity. Bind every delivery to one participant-scoped intent, isolate audits by that identity, and preserve ambiguity rather than guessing when legacy history cannot be resolved safely.
