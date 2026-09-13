from pathlib import Path

p = Path("src/execution.rs")
s = p.read_text(encoding="utf-8")

s = s.replace(
    "#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]\npub struct AuthorizationProvenance {",
    "#[cfg(test)]\n#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]\npub struct AuthorizationProvenance {",
    1,
)
s = s.replace(
    "pub fn get_authorization_provenance(\n",
    "#[cfg(test)]\npub fn get_authorization_provenance(\n",
    1,
)

p.write_text(s, encoding="utf-8")
