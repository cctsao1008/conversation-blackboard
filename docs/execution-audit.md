# Execution audit

Conversation Blackboard preserves committed semantic execution evidence as durable history. The audit surface reads that history directly; it does not re-run current authorization policy.

## Historical bundle

A historical execution is identified by:

```text
participant_id + intent_id
```

The canonical read model is:

```text
ExecutionAuditBundle
├── receipt
├── authorization
└── ingress[]
```

`receipt` identifies the committed semantic execution, `authorization` records why that execution was admitted at commit time, and `ingress[]` preserves every transport delivery associated with the same semantic intent.

## Operator CLI

```text
conversation-blackboard execution audit \
  --db board.db \
  --participant-id maker-main \
  --intent-id intent-83
```

The command is read-only. Unknown executions return an explicit not-found error.

## Source of truth

The audit helper reads only committed durable evidence:

```text
execution_receipts
execution_authorization_provenance
ingress_provenance
```

It does not call `evaluate_authorization()` and does not reconstruct historical policy from current grants.

This distinction is intentional:

```text
grant explain
    -> why would this principal be allowed or denied now?

execution audit
    -> why was this committed execution allowed then?
```

A later grant deactivation, participant-policy change, or authorization refactor must not rewrite the historical audit result.

## Semantic and transport identity

`intent_id` remains the semantic execution identity. `delivery_id` remains transport provenance.

Therefore one semantic execution may have multiple ingress deliveries:

```text
REST delivery A ─┐
MCP delivery B ──┼──> intent-001 ──> one execution receipt
GitHub delivery C┘
```

The audit bundle exposes all of those deliveries while preserving exactly one semantic receipt.

## Security boundary

Execution audit exposes non-secret historical metadata only. It must not expose JWTs, bearer tokens, HMAC secrets, TOTP values, session secrets, or other raw credential material.

Persisted principal identity, transport provenance, authorization source/reason, and grant references are audit metadata; credentials are not.

## Relationship to execution atomicity

Authorization provenance is written by the same execution transaction that commits the semantic effect, receipt, ingress provenance, and one-shot delegated-grant consumption.

The historical audit view therefore reflects committed execution state rather than an independently generated log.

Core rule:

> Historical execution inspection reads committed evidence; it never re-evaluates current policy.
