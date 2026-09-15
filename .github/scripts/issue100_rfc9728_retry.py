from pathlib import Path

p = Path("src/mcp.rs")
s = p.read_text(encoding="utf-8")
old = "pub(crate) fn app_with_oidc(\n"
new = "#[cfg(test)]\npub(crate) fn app_with_oidc(\n"
if old not in s:
    raise SystemExit("missing app_with_oidc anchor")
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
