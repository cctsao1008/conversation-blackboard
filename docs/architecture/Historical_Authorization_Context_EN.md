# Historical Authorization Context

Issue #93 completes the historical authorization record for committed semantic executions.

## Purpose

Current authorization policy answers whether a principal may act now. Historical authorization provenance answers why a past semantic execution was admitted then.

These are intentionally different questions.

A historical execution audit must not reconstruct past authorization from today's grants, roles, or fallback rules. The admitted request context is persisted when the semantic effect commits.

## Canonical historical authorization record

Fresh committed executions persist the following non-secret authorization context together with the authorization decision:

```text
AuthorizationProvenance
  participant_id
  intent_id
  principal
    provider
    subject
  capability
  resource
  source
  reason
  grant_id
```

The semantic identity remains:

```text
(participant_id, intent_id)
```

The principal identifies the authenticated actor that was admitted, while `participant_id` remains the durable Blackboard attribution identity.

`capability` records what operation was authorized. `resource` records the authorization scope used for that request. `source`, `reason`, and `grant_id` retain the canonical `AuthorizationDecision` evidence.

No credential material is stored in this record.

## Commit-time rule

For fresh executions the admitted authorization context is written inside the same execution transaction as the semantic effect and its other committed evidence:

```text
message effect
+ navigation / idempotency reservation
+ ingress provenance
+ execution receipt
+ authorization provenance
+ delegated one-shot consumption
```

Therefore an accepted semantic execution either commits its historical authorization context together with the effect, or commits nothing.

## Historical versus current policy

Audit reads never re-run current authorization policy to reconstruct a past decision.

```text
Current request
  current Principal
      ↓
  current policy gate
      ↓
permission to read historical evidence

Historical execution
  persisted AuthorizationProvenance
      ↓
returned as committed evidence
```

Changing, expiring, deactivating, or deleting a current grant does not rewrite a historical authorization snapshot.

## Legacy migration discipline

Older authorization rows predate the complete admitted-request context.

Migration follows a conservative rule:

- `capability` may be backfilled from the committed execution receipt because that value is deterministically present in historical execution evidence;
- `principal` is not reconstructed from current policy;
- `resource` is not reconstructed from current policy;
- unresolved legacy principal or resource fields remain null rather than being guessed.

This distinction is deliberate. Unknown historical evidence is represented as unknown historical evidence.

## Integrity verification

The execution-audit integrity verifier validates the stronger authorization record without re-evaluating current policy.

Fresh complete evidence can establish checks such as:

```text
authorization_principal_bound
authorization_capability_matches_receipt
authorization_resource_matches_effect
```

Structural or legacy gaps are reported explicitly, including diagnostics such as:

```text
authorization_principal_unbound
authorization_scope_unbound
authorization_capability_mismatch
authorization_resource_mismatch
authorization_metadata_invalid
```

The verifier compares the persisted capability with the execution receipt and the persisted resource with the committed semantic effect where that comparison is defined.

It does not repair the evidence and does not authorize the historical action again.

## Projection rule

`AuthorizationProvenance` is a canonical Rust application contract. REST/OpenAPI, MCP, UTCP, CLI, and other projections must consume or validate against that canonical shape rather than maintaining an independent authorization-history model.

Legacy-null fields remain explicit in projections so clients can distinguish complete post-#93 evidence from older unresolved records.

## Security boundary

Historical authorization provenance may contain durable identifiers and non-secret policy metadata, but never authentication secrets or raw credential material.

Do not persist:

- bearer tokens;
- JWTs;
- HMAC secrets;
- TOTP values;
- web-session tokens;
- private keys.

The architectural rule remains:

> Current policy determines whether the caller may read now; persisted evidence determines why the committed execution was allowed then.
