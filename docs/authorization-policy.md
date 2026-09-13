# Scoped Authorization Policy

Conversation Blackboard separates authentication, authorization, semantic intent, and durable attribution.

## Core model

Authorization is evaluated as:

```text
Principal × Capability × Participant × Resource × Context -> AuthorizationDecision
```

`participant_id` remains the durable Blackboard attribution identity. Authentication mechanisms resolve an actor into a `Principal`; the authorization layer then determines whether that principal may exercise a capability for the participant and resource.

The canonical decision contains only non-secret policy metadata:

```text
allowed
source
reason
optional grant_id
optional consume_grant_id
```

Credential material such as JWTs, bearer tokens, HMAC secrets, TOTP values, or session secrets is never part of the decision object.

## Authority sources

Three authority sources coexist:

1. **Explicit durable scoped grants** in `principal_grants`.
2. **Implicit compatibility authority** derived from existing participant relationships:
   - GitHub owner binding (`owner_provider = github`, matching `owner_subject`).
   - participant HMAC self-authentication.
   - human-web self-authentication, with `manage_channels` limited to admin participants.
3. **Delegated grants** in `delegated_grants`, optionally constrained by resource, semantic `intent_id`, expiry, and one-shot consumption.

Explicit durable grants are evaluated per capability. If one or more active explicit grants exist for a principal + participant + capability, only matching resource grants authorize through the durable-grant path; broader implicit fallback does not bypass the restriction. Intent-aware delegated authority may still authorize a separately scoped semantic execution when its own constraints match.

## Lifecycle precedence

Participant lifecycle remains authoritative. A missing participant is denied. An inactive participant denies all durable, implicit, and delegated authority.

## Canonical decision kernel

`authorization::evaluate_authorization` is the policy kernel for both execution and operator explanation. It owns participant lifecycle, durable grant matching, implicit compatibility authority, delegated constraints, expiry, intent binding, one-shot replay classification, and consumption intent.

```text
authenticated Principal
        ↓
authorization::evaluate_authorization
        ↓
AuthorizationDecision
        ├─ execution.rs -> enforce allow/deny and consume only on commit
        └─ grant explain -> render the same decision read-only
```

`authorize` remains a thin compatibility projection for non-intent boolean checks. Intent-aware execution consumes the canonical decision directly.

## One-shot delegated authority

A fresh matching one-shot delegated grant returns `consume_grant_id`. Only the execution layer may consume it, and consumption remains in the same SQLite transaction as semantic effect, ingress provenance, and execution receipt.

A committed replay of the same semantic intent is classified separately and does not request a second consumption. A consumed grant without a matching committed execution receipt is denied.

## Explainability

`conversation-blackboard grant explain` renders the canonical decision without mutating authority. Its decision source/reason therefore cannot drift into a second policy implementation.

Representative reason classes include:

- `participant_missing`
- `participant_inactive`
- `explicit_durable_grant_match`
- `explicit_resource_scope_mismatch`
- `implicit_github_owner`
- `implicit_participant_hmac`
- `implicit_human_web`
- `implicit_human_web_admin`
- `delegated_grant_match`
- `delegated_resource_mismatch`
- `delegated_intent_mismatch`
- `delegated_grant_expired`
- `delegated_consumed_different_intent`
- `delegated_committed_replay`
- `delegated_consumed_without_committed_receipt`
- `no_matching_authority`

## Access context

`/api/access-context` reports effective grants derived from the same authorization model. It exposes authorization state, not credentials.

## Verification boundary

Authorization changes are accepted only after formatting, strict Clippy, Rust tests, release build, and Windows service/CLI/smoke/package verification pass in core CI. The #81 migration additionally verifies that execution and `grant explain` consume one canonical policy decision while one-shot consumption remains execution-only and atomic.

The implementation plus verification-boundary documentation passed core-ci run `34764521912`: Linux formatting, strict Clippy, tests, and release build passed; Windows release verification, CLI surface, service/operations/two-identity smoke, packaging, and artifact upload also passed. Recording the evidence is documentation-only and is not treated as a new semantic acceptance boundary.
