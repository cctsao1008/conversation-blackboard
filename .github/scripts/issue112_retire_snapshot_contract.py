from pathlib import Path

path = Path('src/contract_schema.rs')
text = path.read_text()
start = text.find('#[cfg(test)]\npub fn authorization_policy_snapshot_schema()')
end = text.find('pub fn authorization_policy_window_schema()', start)
if start < 0 or end < 0 or end <= start:
    raise RuntimeError('legacy snapshot wire schema retirement anchors not found')
path.write_text(text[:start] + text[end:])
