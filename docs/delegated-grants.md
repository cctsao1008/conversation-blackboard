# Delegated grants

Conversation Blackboard separates authentication from authorization. Authentication establishes a `Principal`; Blackboard policy decides what that principal may do as a durable `participant_id`.

## Authority model

Durable grants continue to use the existing scoped policy model:

```text
Principal × Participant × Capability × Resource -> allow / deny
```

For narrow remote-agent delegation, Blackboard also supports delegated grants with optional semantic and lifetime constraints:

```text
Principal × Participant × Capability × Resource
    × optional intent_id
    × optional expires_at
    × optional one_shot
    -> allow / deny
```

A delegated grant stores authority metadata only. It does not store JWTs, bearer credentials, HMAC secrets, TOTP material, or other authentication credentials.

## Intent binding

When `intent_id` is present, the delegated grant may authorize only that semantic intent. A different transport delivery for the same committed intent remains an idempotent replay; a different semantic intent is not authorized by that grant.

## Expiration

When `expires_at` is present, a new execution is denied after that time. A one-shot grant that already committed its bound intent may still recognize replay of that same committed semantic intent so idempotency is preserved.

## One-shot consumption

A one-shot delegated grant is consumed only when a new semantic execution commits. Consumption is part of the same SQLite transaction as:

```text
message effect
+ navigation/idempotency reservation
+ ingress provenance
+ execution receipt
+ delegated grant consumption
```

If reply validation, channel lifecycle validation, audit persistence, or another transactional step fails, the grant is not consumed.

## Existing authority remains valid

This feature does not replace or weaken existing authentication adapters or durable policy:

- GitHub ownership remains an implicit authority source where configured.
- Participant HMAC self-authentication remains supported.
- Human Web/TOTP remains supported.
- External OIDC authenticates a principal but does not itself grant Blackboard authority.
- Durable explicit `principal_grants` remain backward-compatible.

`participant_id` remains the durable attribution identity. `conversation_ref` remains optional provenance only.
