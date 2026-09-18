from pathlib import Path

# This helper intentionally remains a narrow source-guard migration. Re-run from
# current main after the prior all-green workflow lost its final push race.
path = Path('src/contract_parity_tests.rs')
text = path.read_text()
old = '            source.contains("read_administration_events"),\n            "{name} adapter must reuse the canonical administration history reader"\n'
new = '            source.contains("read_administration_event_window"),\n            "{name} adapter must reuse the canonical bounded administration history reader"\n'
if text.count(old) != 1:
    raise RuntimeError(f'bounded history parity guard anchor count: {text.count(old)}')
path.write_text(text.replace(old, new, 1))
