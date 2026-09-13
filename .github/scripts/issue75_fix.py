from pathlib import Path

path = Path("src/adapter_profile.rs")
text = path.read_text(encoding="utf-8")

if not text.startswith("#[derive(Debug, Clone, Copy, PartialEq, Eq)]"):
    raise SystemExit("unexpected adapter_profile.rs header")
text = "#![cfg_attr(not(test), allow(dead_code))]\n\n" + text

old = "        assert!(GITHUB_MAILBOX.asynchronous);"
new = "        let github = profile(\"github-mailbox\").unwrap();\n        assert!(github.asynchronous);"
if text.count(old) != 1:
    raise SystemExit(f"expected one constant assertion, found {text.count(old)}")
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
