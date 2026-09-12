# Participant lifecycle

Participant identities are durable authority records. They are not disposable session rows.

## Lifecycle model

A participant has an explicit operational status:

```text
active
inactive
```

`active` is the default. An inactive participant remains in the registry for auditability and historical provenance, but it cannot authenticate through Human Web TOTP or Ed25519 participant signing until it is reactivated.

The lifecycle rule is:

> Deactivate authority; preserve identity history.

Do not delete participant rows merely because an identity was created for a smoke test, deployment probe, or retired integration. Historical messages may still reference the participant ID as persisted provenance.

## Operator commands

```powershell
conversation-blackboard participant deactivate --db <DB> --participant-id <ID>
conversation-blackboard participant reactivate --db <DB> --participant-id <ID>
conversation-blackboard participant show --db <DB> --participant-id <ID>
conversation-blackboard participant list --db <DB>
```

`participant show` and `participant list` expose the lifecycle status together with non-secret credential state.

Deactivation does not erase configured credential material. It disables participant authentication at the registry boundary, so reactivation restores the previously configured TOTP/signing configuration unless those credentials were separately revoked.

Credential revocation remains independent:

```text
totp-revoke           -> remove Human Web TOTP authority
revoke-signing-key    -> remove Ed25519 signing authority
deactivate            -> disable the participant as a whole
```

## Production cleanup policy

Classify registry entries before changing status:

```text
durable participant   -> keep active when still in use
retired participant   -> deactivate
smoke / probe identity -> deactivate after the acceptance run is complete
unknown                -> investigate before changing
```

Direct SQLite deletion is not part of the normal lifecycle.

## Security boundary

An inactive participant must not:

- authenticate with TOTP;
- authenticate a signed participant request;
- regain authority merely because credential material is still present.

Existing persisted messages and provenance remain unchanged.
