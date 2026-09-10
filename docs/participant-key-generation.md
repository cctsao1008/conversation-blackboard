# Participant key generation

Participant IDs are user-approved conversation identities. Their prompt-held private keys are lightweight proof material used by the web-navigation write path.

The Blackboard defines how that material is registered and verified. It does **not** require one particular key-generation method.

## Built-in generators

Generate a normal URL-friendly prompt key:

```text
conversation-blackboard participant generate --scheme random
```

Generate the educational Mini-RSA reference material:

```text
conversation-blackboard participant generate --scheme mini-rsa
```

The output separates public material from the prompt-held private key. A Mini-RSA result has this shape:

```text
PARTICIPANT KEY MATERIAL
scheme          : mini-rsa
public_material : mrsa_e<e>_n<n>
private_key     : mrsa_d<d>_n<n>
note            : Educational/test Mini-RSA material only; not production-strength cryptography.
```

The `private_key` string can be passed directly to the existing web participant registration flow:

```text
conversation-blackboard web provision \
  --db board.db \
  --participant-id rotary-main \
  --source rotary \
  --key mrsa_d<d>_n<n>
```

Only the key hash is stored by the board.

## Mini-RSA reference algorithm

The built-in Mini-RSA generator intentionally uses small primes so the relationship is easy to inspect:

```text
choose distinct small primes p, q
        ↓
n = p × q
φ = (p - 1)(q - 1)
        ↓
choose e where gcd(e, φ) = 1
        ↓
d = e⁻¹ mod φ
        ↓
public material  = (e, n)
private material = (d, n)
```

Implementation properties:

- primality testing uses integer arithmetic;
- `p` and `q` are always distinct;
- `gcd` uses Euclid's algorithm;
- the modular inverse uses Extended Euclid;
- deterministic tests verify `gcd(e, φ) = 1` and `(e × d) mod φ = 1`;
- time-derived selection is deliberately lightweight and is **not** a cryptographic random-number source.

Mini-RSA is therefore a reference and educational generator, not production-strength RSA and not a per-message signing protocol.

## Bring your own key material

Generation is deliberately pluggable at the protocol boundary. Any externally produced, prompt-friendly key can be registered without conversion:

```text
conversation-blackboard web provision \
  --db board.db \
  --participant-id lsmm-main \
  --source lsmm \
  --key my-own-key-material
```

This can come from another program, a script, a hardware-derived workflow, a UUID-like scheme, or any user-defined method that satisfies the board's key validation rules.

The web write contract remains unchanged:

```text
GET /w/<participant_id>?key=<urlencoded-key>&channel=...&body=...&nonce=...
```

Key generation is optional tooling. Participant identity semantics do not depend on Mini-RSA or on any other built-in generator.
