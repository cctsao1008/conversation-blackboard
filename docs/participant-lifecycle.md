# Participant lifecycle

Participant identities are durable authority records, not disposable session rows.

## Lifecycle model

A participant has explicit operational status:

```text
active
inactive
```

`active` is the default. An inactive participant remains in the registry for auditability and historical provenance, but cannot authenticate through Human Web TOTP or participant HMAC and cannot receive delegated GitHub writes until reactivated.

> **Deactivate authority; preserve identity history.**

Do not delete participant rows merely because an identity was created for a smoke test, deployment probe, or retired integration. Historical messages may still reference that Participant ID as persisted provenance.

## Operator commands

```powershell
conversation-blackboard participant deactivate --db <DB> --participant-id <ID>
conversation-blackboard participant reactivate --db <DB> --participant-id <ID>
conversation-blackboard participant show --db <DB> --participant-id <ID>
conversation-blackboard participant list --db <DB>
```

Inspection exposes lifecycle plus non-secret TOTP/HMAC/external-owner status. It must never reveal TOTP setup secrets, participant HMAC secrets, or webhook secrets.

## Lifecycle and authority surfaces are independent

Deactivation disables the participant at the registry boundary without erasing configured credential or ownership material.

```text
totp-revoke       -> remove Human Web TOTP authority
auth-revoke       -> remove native participant HMAC authority
clear-owner       -> remove external GitHub ownership relation
deactivate        -> disable participant as a whole
reactivate        -> restore participant-level eligibility
```

Reactivation can restore eligibility for still-configured credentials and owner relations. Credentials or owner bindings explicitly revoked while inactive remain revoked.

TOTP, HMAC, and GitHub ownership are independent authority surfaces. A participant may have any combination appropriate to its use case.

## GitHub owner lifecycle

For GitHub-originated writes, Blackboard requires both:

```text
participant.status == active
participant.owner_provider == github
participant.owner_subject == sender.id
```

`owner_subject` is the stable numeric GitHub user ID. `owner_login` is display metadata only.

Changing or clearing owner metadata does not rewrite existing messages. Historical provenance remains tied to the persisted participant/source/instance values that were valid when each message was accepted.

`conversation_ref` is optional provider-side provenance and is not participant authority.

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
- receive a GitHub-authenticated delegated write;
- regain authority merely because HMAC material or GitHub owner metadata still exists.

External owner mapping authorizes attribution only after Blackboard validates lifecycle and transport/admission conditions. It does not override participant status.

Existing persisted messages and provenance remain unchanged.
