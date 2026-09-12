# Participant lifecycle

Participant identities are durable authority records, not disposable session rows.

## Lifecycle model

A participant has explicit operational status:

```text
active
inactive
```

`active` is the default. An inactive participant remains in the registry for auditability and historical provenance, but cannot authenticate through Human Web TOTP or participant HMAC until reactivated.

> **Deactivate authority; preserve identity history.**

Do not delete participant rows merely because an identity was created for a smoke test, deployment probe, or retired integration. Historical messages may still reference that Participant ID as persisted provenance.

## Operator commands

```powershell
conversation-blackboard participant deactivate --db <DB> --participant-id <ID>
conversation-blackboard participant reactivate --db <DB> --participant-id <ID>
conversation-blackboard participant show --db <DB> --participant-id <ID>
conversation-blackboard participant list --db <DB>
```

Inspection exposes lifecycle plus non-secret TOTP/HMAC status. It must never reveal TOTP setup secrets or participant HMAC secrets.

## Lifecycle and credentials are independent

Deactivation disables the participant at the registry boundary without erasing configured credential material.

```text
totp-revoke       -> remove Human Web TOTP authority
auth-revoke       -> remove participant HMAC authority
deactivate        -> disable participant as a whole
reactivate        -> restore participant-level eligibility
```

Reactivation can restore eligibility for still-configured credentials. Credentials explicitly revoked while inactive remain revoked.

TOTP and HMAC are independent authentication surfaces. A participant may have one, both, or neither configured.

## Production cleanup policy

Classify registry entries before changing status:

```text
durable participant    -> keep active while in use
retired participant    -> deactivate
smoke / probe identity -> deactivate after acceptance
unknown                 -> investigate before mutation
```

Direct SQLite deletion is not the normal lifecycle operation.

An explicit operator-approved registry deletion is a separate cleanup exception and should require:

- an explicitly approved retained participant set;
- understood rows being removed;
- persisted messages/provenance intentionally left unchanged;
- a verified rollback backup before mutation.

Prefer `deactivate` for ordinary retirement.

## Security boundary

An inactive participant must not:

- authenticate with TOTP;
- authenticate an HMAC participant read/write;
- regain authority merely because a local DPAPI credential or server-side auth secret still exists.

A local DPAPI credential represents only local signing capability. It cannot override Blackboard lifecycle state.

Existing persisted messages and provenance remain unchanged.
