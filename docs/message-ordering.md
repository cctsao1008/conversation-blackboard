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

The browser treats ordering and navigation as separate concepts.

- Opening a channel in `Newest first` shows the live/latest window and keeps live polling enabled.
- Newly arrived messages are inserted at the top of the live/latest window.
- Changing the sort selector resets the current window and reloads it in the selected direction.
- `Oldest first` is a historical traversal view and does not live-poll while that ordering is active.
- `Jump to #...` navigates to a bounded historical window around a message when that message is not already loaded.
- `Back to latest` is contextual: it is hidden while already in the live/latest window and shown only in a historical view.
- Returning to the live/latest window explicitly restores `Newest first`, because live polling is defined only for descending windows.
- `Refresh` re-fetches the current logical view. A jumped-to historical window is refreshed around the same target instead of silently returning to the live window.
- Loading more history appends the next page in the selected global ordering; the browser never reverses only one page locally.

Ordering and navigation do not change message identity, provenance, reply relationships, authorization, channel visibility, or persisted records.
