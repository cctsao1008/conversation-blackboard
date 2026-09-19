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

old = '''        let guest = list_public_channels(&conn).unwrap();
        assert_eq!(guest.len(), 1);
        assert_eq!(guest[0].channel, "blackboard-lounge");
'''
new = '''        let guest = read_channel_directory_window(
            &conn,
            ChannelDirectoryWindowRequest {
                after_name: None,
                limit: None,
                public_only: true,
            },
        )
        .unwrap();
        assert_eq!(guest.channels.len(), 1);
        assert_eq!(guest.channels[0].channel, "blackboard-lounge");
        assert!(!guest.has_more);
'''
if old not in text:
    raise RuntimeError('legacy public directory test anchor not found')
text = text.replace(old, new, 1)

path.write_text(text)
