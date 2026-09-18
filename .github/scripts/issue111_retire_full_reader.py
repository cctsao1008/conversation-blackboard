from pathlib import Path

path = Path('src/authorization_admin.rs')
text = path.read_text()
start = text.find('pub fn read_administration_events(')
end = text.find('pub fn read_administration_event_window(', start)
if start < 0 or end < 0 or end <= start:
    raise RuntimeError('unbounded history reader retirement anchors not found')
path.write_text(text[:start] + text[end:])
