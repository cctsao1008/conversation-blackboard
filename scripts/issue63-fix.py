from pathlib import Path
import re

identity = Path("src/identity.rs")
text = identity.read_text(encoding="utf-8")
pattern = re.compile(
    r"\npub fn get_web_participant_status\(.*?\n\}\n",
    re.S,
)
text, count = pattern.subn("\n", text, count=1)
if count != 1:
    raise SystemExit("could not remove obsolete get_web_participant_status")
identity.write_text(text, encoding="utf-8")

old = Path("src/signed_auth.rs")
new = Path("src/participant_auth.rs")
if not old.exists():
    raise SystemExit("src/signed_auth.rs missing")
old.rename(new)

for path in Path("src").glob("*.rs"):
    text = path.read_text(encoding="utf-8")
    text = text.replace("signed_auth", "participant_auth")
    text = text.replace("SIGNATURE_SCHEME", "AUTH_SCHEME")
    path.write_text(text, encoding="utf-8")

for path in Path("src").glob("*.rs"):
    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    forbidden = ["ed25519", "public_key", "private_key", "signed_auth"]
    found = [token for token in forbidden if token in lowered]
    if found:
        raise SystemExit(f"obsolete asymmetric-auth tokens remain in {path}: {found}")
