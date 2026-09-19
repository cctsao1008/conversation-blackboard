from pathlib import Path

path = Path('src/db.rs')
text = path.read_text()
old = '''        let guest = list_public_channels(&conn).unwrap();\n        assert_eq!(guest.len(), 1);\n        assert_eq!(guest[0].channel, "blackboard-lounge");\n'''
new = '''        let guest = read_channel_directory_window(\n            &conn,\n            ChannelDirectoryWindowRequest {\n                after_name: None,\n                limit: None,\n                public_only: true,\n            },\n        )\n        .unwrap();\n        assert_eq!(guest.channels.len(), 1);\n        assert_eq!(guest.channels[0].channel, "blackboard-lounge");\n        assert!(!guest.has_more);\n'''
if old not in text:
    raise RuntimeError('guest channel test anchor not found')
path.write_text(text.replace(old, new, 1))
