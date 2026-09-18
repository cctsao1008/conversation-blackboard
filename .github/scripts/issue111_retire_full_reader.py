from pathlib import Path

path = Path('src/authorization_admin.rs')
text = path.read_text()
start = text.find('pub fn read_administration_events(')
end = text.find('pub fn read_administration_event_window(', start)
if start < 0 or end < 0 or end <= start:
    raise RuntimeError('unbounded history reader retirement anchors not found')
text = text[:start] + text[end:]

old = '''        let events = read_administration_events(&conn, Some("maker-main")).unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].operation, "create");
        assert_eq!(events[1].operation, "deactivate");
        assert_eq!(events[0].grant_id, created.id);
        assert_eq!(events[0].actor_surface, "local-cli");
        assert_eq!(events[0].actor_provider, None);
        assert_eq!(events[0].actor_subject, None);
        assert!(events[0].id < events[1].id);
'''
new = '''        let window = read_administration_event_window(
            &conn,
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: Some("maker-main"),
                before: None,
                after: None,
                limit: Some(20),
                order: Some("asc"),
            },
        )
        .unwrap();
        let events = window.events;
        assert_eq!(window.order, "asc");
        assert!(!window.has_more);
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].operation, "create");
        assert_eq!(events[1].operation, "deactivate");
        assert_eq!(events[0].grant_id, created.id);
        assert_eq!(events[0].actor_surface, "local-cli");
        assert_eq!(events[0].actor_provider, None);
        assert_eq!(events[0].actor_subject, None);
        assert!(events[0].id < events[1].id);
'''
if text.count(old) != 1:
    raise RuntimeError(f'commit-order history test anchor count: {text.count(old)}')
text = text.replace(old, new, 1)

old = '        assert!(read_administration_events(&conn, None).is_err());\n'
new = '''        assert!(read_administration_event_window(
            &conn,
            AuthorizationAdministrationHistoryWindowRequest {
                participant_id: None,
                before: None,
                after: None,
                limit: None,
                order: None,
            },
        )
        .is_err());
'''
if text.count(old) != 1:
    raise RuntimeError(f'stale-schema history test anchor count: {text.count(old)}')
text = text.replace(old, new, 1)
path.write_text(text)
