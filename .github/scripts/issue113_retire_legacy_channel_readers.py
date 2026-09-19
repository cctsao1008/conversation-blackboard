from pathlib import Path

path = Path('src/db.rs')
text = path.read_text()

start = text.find('pub fn list_channels(')
end = text.find('pub const DEFAULT_CHANNEL_DIRECTORY_WINDOW_SIZE', start)
if start < 0 or end < 0 or end <= start:
    raise RuntimeError('legacy channel entrypoint anchors not found')
text = text[:start] + text[end:]

start = text.find('fn collect_channel_directory(')
end = text.find('pub fn channel_metadata(', start)
if start < 0 or end < 0 or end <= start:
    raise RuntimeError('legacy channel collector anchors not found')
text = text[:start] + text[end:]

path.write_text(text)
