# Quickstart: Verifying Telegram Polling Success Timestamp

This mission is not authorized to run implementation or tests during planning. After implementation approval, verify the change locally with focused pytest targets only.

Expected operator behavior:

- Before any successful Telegram polling progress is recorded, status may omit `last_successful_poll_at`.
- After a successful current-generation dedicated `getUpdates` response, Telegram platform status shows `last_successful_poll_at` as a UTC ISO timestamp.
- The timestamp is historical evidence. Gateway startup, restart, reconnect, `getMe`, `start_polling`, retries, timeouts, conflicts, and failed API responses do not create a new timestamp.
- The timestamp is not a message receive time, not gateway `updated_at`, and not proof that a general Telegram API request succeeded.

Focused verification targets after implementation:

```bash
pytest tests/gateway/test_telegram_polling_progress.py
pytest tests/test_telegram_polling_progress_ptb.py
pytest tests/gateway/test_telegram_network_reconnect.py
pytest tests/gateway/test_status.py
pytest tests/gateway/test_api_server.py
pytest tests/hermes_cli/test_web_server.py
pytest tests/gateway/test_telegram_error_redaction.py
```

Privacy check:

- Status and logs must not contain Telegram update payloads.
- Status and logs must not contain Telegram chat IDs, user IDs, message IDs, bot tokens, request URLs, query strings, or exception detail beyond existing redacted status behavior.
