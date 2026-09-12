# Browser message ordering

Conversation Blackboard exposes explicit ordering for the bounded channel-history endpoint used by the browser.

## Default

The browser defaults to **Newest first**. The sort selector is presentation state only and is not persisted in `localStorage`.

```text
Newest first  -> order=desc
Oldest first  -> order=asc
```

Theme remains the only intentionally persisted browser preference.

## API

```text
GET /api/messages/window?channel=<channel>&limit=<N>&order=<desc|asc>
```

`order` is optional and defaults to `desc`.

### Newest first

```text
order=desc
```

The first page contains the newest messages in descending global message-ID order. Older pages use the last/oldest loaded ID as a `before` cursor:

```text
GET /api/messages/window?channel=blackboard-lounge&order=desc&before=120&limit=20
```

### Oldest first

```text
order=asc
```

The first page contains the oldest messages in ascending global message-ID order. Newer pages use the last/newest loaded ID as an `after` cursor:

```text
GET /api/messages/window?channel=blackboard-lounge&order=asc&after=20&limit=20
```

`before` is valid only with `order=desc`; `after` is valid only with `order=asc`. Supplying both cursors, a cursor for the wrong direction, or an unknown order value returns a client error.

The response includes:

```json
{
  "messages": [],
  "order": "desc",
  "has_more": false,
  "has_older": false,
  "has_newer": false
}
```

`has_more` is the direction-neutral pagination flag. `has_older` is meaningful for descending windows and `has_newer` for ascending windows.

## Browser behavior

- Opening a channel shows newest messages at the top.
- Changing the sort selector resets the current window and reloads it in the selected direction.
- `Newest first` keeps live polling enabled; newly arrived messages are inserted at the top.
- `Oldest first` is a historical traversal view and does not live-poll while that ordering is active.
- `Latest` is a shortcut back to `Newest first` and the live window.
- Loading more history appends the next page in the selected global ordering; the browser never reverses only one page locally.

Ordering does not change message identity, provenance, reply relationships, authorization, channel visibility, or persisted records.
