from pathlib import Path


# HTTP runtime: replace the remaining asymmetric-auth surface with HMAC proof
# terminology and the participant HMAC credential record.
path = Path("src/http.rs")
text = path.read_text(encoding="utf-8")
for old, new in [
    ('.get("sig")', '.get("proof")'),
    ('let signature = params', 'let proof = params'),
    ('identity::get_web_participant_verification', 'identity::get_web_participant_auth'),
    ('record.signature_scheme', 'record.auth_scheme'),
    ('record.public_key', 'record.auth_secret'),
    ('&signature,', '&proof,'),
    ('only_keys(value, &["scheme", "signature"])', 'only_keys(value, &["scheme", "proof"])'),
    ('.get("signature")', '.get("proof")'),
    ('let signature = auth', 'let proof = auth'),
    ('unsupported_signature_scheme', 'unsupported_auth_scheme'),
]:
    text = text.replace(old, new)
path.write_text(text, encoding="utf-8")

# HTTP contract tests: use HMAC secrets/proofs, not asymmetric signing keys.
path = Path("src/http_contract_tests.rs")
text = path.read_text(encoding="utf-8")
text = text.replace('set_web_participant_signing_key', 'set_web_participant_auth_secret')
text = text.replace('&sig=', '&proof=')
text = text.replace('"signature":', '"proof":')
text = text.replace('signed_navigation_write', 'hmac_navigation_write')
text = text.replace('distinct_signed_participants', 'distinct_hmac_participants')
path.write_text(text, encoding="utf-8")

# Remove remaining asymmetric terminology from the participant-auth primitive's
# function names. There are deliberately no compatibility aliases.
for path_name in [
    "src/signed_auth.rs",
    "src/identity.rs",
    "src/participant_admin.rs",
    "src/mcp.rs",
    "src/mcp_contract_tests.rs",
    "src/web_auth.rs",
    "src/http.rs",
    "src/http_contract_tests.rs",
]:
    path = Path(path_name)
    text = path.read_text(encoding="utf-8")
    for old, new in [
        ('generate_keypair', 'generate_secret'),
        ('validate_public_key', 'validate_secret'),
        ('verify_message_signature', 'verify_message_proof'),
        ('verify_write_signature', 'verify_write_proof'),
        ('verify_read_signature', 'verify_read_proof'),
        ('sign_write', 'compute_write_proof'),
        ('sign_read', 'compute_read_proof'),
        ('sign_message', 'compute_message_proof'),
    ]:
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")

# Fail the one-shot refactor if active Rust code still calls the removed
# Ed25519 registry API. Text in historical docs is handled separately.
for path_name in ["src/http.rs", "src/http_contract_tests.rs"]:
    text = Path(path_name).read_text(encoding="utf-8")
    forbidden = [
        'get_web_participant_verification',
        'set_web_participant_signing_key',
        '.signature_scheme',
        '.public_key',
        '.get("sig")',
        '.get("signature")',
    ]
    leftovers = [token for token in forbidden if token in text]
    if leftovers:
        raise SystemExit(f"remaining asymmetric auth tokens in {path_name}: {leftovers}")
