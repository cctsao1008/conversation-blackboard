from pathlib import Path
import re

p = Path("src/authorization.rs")
s = p.read_text(encoding="utf-8")

s = re.sub(
    r"\n#\[derive\(Debug, Clone, PartialEq, Eq\)\]\npub enum IntentAuthorization \{.*?\n\}\n",
    "\n",
    s,
    count=1,
    flags=re.S,
)

s = re.sub(
    r"\npub fn authorize_for_intent\(.*?\n\}\n\npub fn consume_delegated_grant_in_tx",
    "\npub fn consume_delegated_grant_in_tx",
    s,
    count=1,
    flags=re.S,
)

s = re.sub(
    r"\nfn implicit_authority\(.*?\n\}\n\nfn committed_receipt_exists",
    "\nfn committed_receipt_exists",
    s,
    count=1,
    flags=re.S,
)

p.write_text(s, encoding="utf-8")
