from pathlib import Path

path = Path('src/db.rs')
text = path.read_text()
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
    if 'let guest = read_channel_directory_window(' in text:
        raise SystemExit(0)
    raise RuntimeError('guest channel test anchor not found')
path.write_text(text.replace(old, new, 1))
