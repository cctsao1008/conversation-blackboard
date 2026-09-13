from pathlib import Path

p = Path('src/http_contract_tests.rs')
s = p.read_text(encoding='utf-8')
old = "        params![&fixture.participant_id, intent_hash],\n"
new = "        rusqlite::params![&fixture.participant_id, intent_hash],\n"
if old not in s:
    raise SystemExit('issue90 params macro repair target not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')
