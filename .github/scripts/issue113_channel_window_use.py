from pathlib import Path

path = Path('src/db.rs')
text = path.read_text()

old = '''pub fn list_channels(conn: &Connection) -> Result<Vec<ChannelSummary>> {\n    list_channels_with_filter(conn, false)\n}\n\npub fn list_public_channels(conn: &Connection) -> Result<Vec<ChannelSummary>> {\n    list_channels_with_filter(conn, true)\n}\n'''
new = '''pub fn list_channels(conn: &Connection) -> Result<Vec<ChannelSummary>> {\n    let mut channels = collect_channel_directory(conn, false)?;\n    channels.sort_by(|left, right| {\n        let left_status = if left.status == "active" { 0 } else { 1 };\n        let right_status = if right.status == "active" { 0 } else { 1 };\n        left_status\n            .cmp(&right_status)\n            .then_with(|| right.last_id.cmp(&left.last_id))\n            .then_with(|| left.channel.cmp(&right.channel))\n    });\n    Ok(channels)\n}\n\npub fn list_public_channels(conn: &Connection) -> Result<Vec<ChannelSummary>> {\n    let mut channels = collect_channel_directory(conn, true)?;\n    channels.sort_by(|left, right| {\n        right\n            .last_id\n            .cmp(&left.last_id)\n            .then_with(|| left.channel.cmp(&right.channel))\n    });\n    Ok(channels)\n}\n'''
if old not in text:
    raise RuntimeError('legacy channel entrypoint anchor not found')
text = text.replace(old, new, 1)

start = text.find('fn list_channels_with_filter(')
end = text.find('pub fn channel_metadata(', start)
if start < 0 or end < 0 or end <= start:
    raise RuntimeError('legacy full channel reader anchors not found')

collector = '''fn collect_channel_directory(\n    conn: &Connection,\n    public_only: bool,\n) -> Result<Vec<ChannelSummary>> {\n    let mut channels = Vec::new();\n    let mut after_name: Option<String> = None;\n    loop {\n        let window = read_channel_directory_window(\n            conn,\n            ChannelDirectoryWindowRequest {\n                after_name: after_name.as_deref(),\n                limit: Some(MAX_CHANNEL_DIRECTORY_WINDOW_SIZE),\n                public_only,\n            },\n        )?;\n        after_name = window.channels.last().map(|channel| channel.channel.clone());\n        channels.extend(window.channels);\n        if !window.has_more {\n            break;\n        }\n    }\n    Ok(channels)\n}\n\n'''
text = text[:start] + collector + text[end:]
path.write_text(text)
