# External OIDC resource-server mode

Conversation Blackboard may validate short-lived JWT access tokens issued by one configured external OIDC/OAuth issuer. Blackboard remains a resource server: it does not issue user identities, authorization codes, access tokens, refresh tokens, or client registrations.

## Configuration

Set both:

- `BLACKBOARD_OIDC_ISSUER` — exact HTTPS issuer identifier.
- `BLACKBOARD_OIDC_AUDIENCE` — required access-token audience for Blackboard.

Optionally set `BLACKBOARD_OIDC_JWKS_URL` to an HTTPS JWKS endpoint. If omitted, Blackboard resolves `<issuer>/.well-known/openid-configuration`, requires the discovery document issuer to match the configured issuer, and loads its `jwks_uri`.

If issuer/audience are absent, the OIDC adapter is disabled. Partial configuration is a startup error.

## Request contract

`POST /api/oidc/messages/{participant_id}` uses `Authorization: Bearer <short-lived-access-token>` and a JSON body containing `channel`, `body`, required semantic `intent_id`, and optional `kind` / `reply_to`.

The token is verified for RS256 signature, configured issuer, configured audience, expiry, and subject. Raw token material is never persisted in Blackboard provenance, receipts, grants, messages, or response bodies.

The authenticated actor becomes:

```text
Principal {
  provider = "oidc:<issuer>"
  subject  = <token sub>
}
```

That principal has no authority by itself. The normal Blackboard grant kernel still evaluates:

```text
Principal × Capability × Participant × Resource -> allow / deny
```

Existing GitHub, participant HMAC, Human Web/TOTP, and native bearer mechanisms remain separate adapters and are unchanged.
