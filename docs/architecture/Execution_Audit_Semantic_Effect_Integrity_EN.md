# Execution Audit Semantic Effect Integrity

Issue: #90

## Purpose

The execution audit integrity verifier must prove more than row presence. A committed execution receipt is valid only when it still binds to the persisted semantic effect that produced it.

## Canonical binding

For a committed message execution, the receipt stores `intent_hash`. Integrity verification recomputes the canonical message request hash from the persisted message fields:

```text
message_request_hash(
    channel,
    kind,
    body,
    conversation_ref,
    reply_to,
)
```

The invariant is:

```text
execution_receipts.intent_hash
    ==
message_request_hash(persisted semantic effect)
```

The verifier also requires the persisted message attribution to match the receipt participant:

```text
messages.instance == execution_receipts.participant_id
```

## Integrity diagnostics

Successful evidence contributes checks such as:

```text
message_effect_present
message_effect_binding_valid
intent_hash_matches_effect
```

Detected violations include:

```text
message_effect_missing
message_effect_binding_mismatch
intent_hash_mismatch
```

`intent_hash_mismatch` covers changes to any field included in the canonical message request hash, including `channel`, `kind`, `body`, `conversation_ref`, and `reply_to`.

## Boundary

This verification is historical and read-only. It does not re-evaluate current authorization to decide what happened, and it does not repair or rewrite historical evidence.

```text
current authorization
    -> who may inspect now

persisted receipt + persisted semantic effect
    -> whether committed history remains internally consistent
```

## Relationship to prior audit work

```text
#82  authorization provenance is committed historically
#83  execution evidence is assembled into a local audit bundle
#84  audit evidence is projected through authorized remote surfaces
#85  structural audit integrity is verified
#86  integrity verification is projected remotely
#87  ingress provenance is bound to participant + intent identity
#90  receipt hash is verified against the persisted semantic effect
```

The resulting invariant is that execution identity, provenance, and semantic effect must agree on the same committed event rather than merely co-exist as rows in SQLite.
