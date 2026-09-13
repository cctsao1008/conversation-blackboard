from pathlib import Path

path = Path("src/http.rs")
text = path.read_text()

# The first broad replacement in issue72_intent_patch.py intentionally matched the
# first write-proof call in the file, which is the navigation query-string path.
# Restore that legacy path because it has no semantic intent_id field.
wrong_navigation = '''            || !participant_auth::verify_write_proof_with_intent(
                &record.auth_secret,
                &proof,
                &lookup_participant,
                &verify_channel,
                &verify_kind,
                &verify_body,
                reply_to,
                &verify_nonce,
                verify_intent_id.as_deref(),
            )'''
legacy_navigation = '''            || !participant_auth::verify_write_proof(
                &record.auth_secret,
                &proof,
                &lookup_participant,
                &verify_channel,
                &verify_kind,
                &verify_body,
                reply_to,
                &verify_nonce,
            )'''
if wrong_navigation not in text:
    raise SystemExit("navigation proof anchor not found")
text = text.replace(wrong_navigation, legacy_navigation, 1)

# Bind the explicit semantic intent only in the signed JSON write path. Limit the
# search to the section after verify_intent_id is introduced so we do not touch
# navigation_write again.
marker = "    let verify_intent_id = explicit_intent_id.clone();\n"
pos = text.find(marker)
if pos < 0:
    raise SystemExit("signed-write verify_intent_id marker not found")
head, tail = text[:pos], text[pos:]
old_signed = '''            || !participant_auth::verify_write_proof(
                &record.auth_secret,
                &proof,
                &lookup_participant,
                &verify_channel,
                &verify_kind,
                &verify_body,
                reply_to,
                &verify_nonce,
            )'''
new_signed = '''            || !participant_auth::verify_write_proof_with_intent(
                &record.auth_secret,
                &proof,
                &lookup_participant,
                &verify_channel,
                &verify_kind,
                &verify_body,
                reply_to,
                &verify_nonce,
                verify_intent_id.as_deref(),
            )'''
if old_signed not in tail:
    raise SystemExit("signed-write proof anchor not found")
tail = tail.replace(old_signed, new_signed, 1)
path.write_text(head + tail)
