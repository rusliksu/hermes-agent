# Contract: Telegram Polling Progress Status Projection

## Runtime Status Platform Field

When Telegram dedicated polling has recorded a successful current-generation `getUpdates` response, the Telegram platform payload may include:

```json
{
  "platforms": {
    "telegram": {
      "last_successful_poll_at": "2026-09-04T19:00:00+00:00"
    }
  }
}
```

Rules:

- The field is optional for backward compatibility.
- The value is a timezone-aware UTC ISO-8601 timestamp.
- The value advances only from accepted successful current-generation dedicated `getUpdates` progress.
- Failures and non-polling Telegram API activity leave the value unchanged.
- The field contains no Telegram payload, identifiers, tokens, URLs, query strings, or exception text.

## Existing Read Surfaces

- `hermes_cli/web_server.py` `/api/status` exposes the value inside `gateway_platforms.telegram.last_successful_poll_at` when the runtime platform payload contains it.
- `gateway/platforms/api_server.py` `/health/detailed` exposes the value inside `platforms.telegram.last_successful_poll_at` when the runtime platform payload contains it.

No new endpoint or configuration key is part of this contract.
