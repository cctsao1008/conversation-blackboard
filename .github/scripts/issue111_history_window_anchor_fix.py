from pathlib import Path

path = Path('.github/scripts/issue111_history_window_patch.py')
text = path.read_text()
replacements = [
    (
        'ON authorization_admin_events (grant_store, grant_id, id);\\n""",',
        'ON authorization_admin_events (grant_store, grant_id, id);",\\n""",',
    ),
    (
        'ON authorization_admin_events (participant_id, id);\\n""",',
        'ON authorization_admin_events (participant_id, id);",\\n""",',
    ),
]
for old, new in replacements:
    if text.count(old) != 1:
        raise RuntimeError(f'expected one exact helper anchor for {old!r}, found {text.count(old)}')
    text = text.replace(old, new, 1)
path.write_text(text)
