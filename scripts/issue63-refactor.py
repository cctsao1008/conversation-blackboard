from pathlib import Path


def replace_required(text: str, old: str, new: str, path: str) -> str:
    if old not in text:
        raise SystemExit(f"required pattern missing in {path}: {old!r}")
    return text.replace(old, new)


# HTTP runtime: remove the remaining Ed25519-shaped field names and use the
# participant HMAC credential record everywhere.
path = Path("src/http.rs")
text = path.read_text(encoding="utf-8")
for old, new in [
    ('params.get("sig")', 'params.get("proof")'),
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
    text = replace_required(text, old, new, str(path))
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

# Remove remaining asymmetric terminology from the participant primitive's
# public function names. This is a clean break: no compatibility aliases.
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
    text = text.replace('generate_keypair', 'generate_secret')
    text = text.replace('validate_public_key', 'validate_secret')
    text = text.replace('verify_message_signature', 'verify_message_proof')
    text = text.replace('verify_write_signature', 'verify_write_proof')
    text = text.replace('verify_read_signature', 'verify_read_proof')
    text = text.replace('sign_write', 'compute_write_proof')
    text = text.replace('sign_read', 'compute_read_proof')
    text = text.replace('sign_message', 'compute_message_proof')
    path.write_text(text, encoding="utf-8")
