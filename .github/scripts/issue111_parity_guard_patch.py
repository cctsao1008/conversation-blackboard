from pathlib import Path

path = Path('src/contract_parity_tests.rs')
text = path.read_text()
old = '''        assert!(
            source.contains("read_administration_events"),
            "{name} adapter must reuse the canonical administration history reader"
        );
'''
new = '''        assert!(
            source.contains("read_administration_event_window"),
            "{name} adapter must reuse the canonical bounded administration history reader"
        );
        assert!(
            !source.contains("read_administration_events"),
            "{name} adapter must not retain the unbounded administration history reader"
        );
'''
if text.count(old) != 1:
    raise RuntimeError(f'bounded history parity guard anchor count: {text.count(old)}')
path.write_text(text.replace(old, new, 1))
