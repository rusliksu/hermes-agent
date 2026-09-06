# Research: Telegram Polling Success Timestamp

## Scope

Persist the UTC timestamp of the last successful current-generation dedicated Telegram `getUpdates` response for dashboard and health observability. The change must not alter polling behavior, introduce endpoints, add configuration, or expose Telegram payloads, IDs, tokens, URLs, or query strings.

## Decisions

### D1: Record at the existing current-generation progress point

`plugins/platforms/telegram/adapter.py` already funnels successful dedicated `getUpdates` observations through `_observe_polling_request_result()` into `_record_polling_progress(generation)`. `_record_polling_progress()` ignores teardown, non-accepting progress, and stale generations before clearing network/conflict counters and marking the send path healthy.

Decision: extend `_record_polling_progress()` to persist the timestamp after the existing generation guards pass. This keeps stale generation completions, teardown completions, and non-accepted progress from advancing the persisted value.

### D2: Treat only Telegram API success envelopes as progress

`_observe_polling_request_result()` currently requires:

- a non-null generation
- HTTP status code in the `2xx` range
- a parseable JSON envelope
- `ok is True`
- a present `result` key

Decision: preserve that definition. Empty `result` arrays count because they are valid successful long-poll responses. HTTP/API errors, parse errors, timeouts, retry-loop failures, `getMe`, `start_polling`, and other non-`getUpdates` paths must not advance the timestamp.

### D3: Persist via runtime status platform payload

`gateway/status.py::write_runtime_status()` is the existing persistence boundary for gateway state, active agents, served profiles, and per-platform status. `/api/status` in `hermes_cli/web_server.py` and `/health/detailed` in `gateway/platforms/api_server.py` already project the persisted runtime status.

Decision: extend `write_runtime_status()` with an optional per-platform `last_successful_poll_at` field and call it through the Telegram adapter's existing `_write_runtime_status_safe(...)` helper. This keeps the feature in the existing status contract and avoids new endpoint or config schema surface.

### D4: Use timezone-aware UTC wall-clock ISO timestamps

Status files already use UTC ISO strings for `updated_at`. Operators need a human-readable timestamp, not monotonic duration. Tests can inject/freeze the time source at the adapter/status boundary.

Decision: store a timezone-aware UTC ISO-8601 string. Runtime status absence means "unknown/not yet observed"; restart preserves the last persisted timestamp until a newer successful current-generation `getUpdates` response overwrites it.

### D5: Redact by construction

The new value is only a timestamp. It must not include Telegram response payloads, update counts, chat/user/message IDs, bot token fragments, request URLs, query strings, or exception text.

Decision: tests must assert status/log surfaces contain the timestamp only and do not gain sensitive request data.

## Behavior Notes

- Backward compatibility: older `gateway_state.json` files without the field continue to read normally; API surfaces omit or expose `null`/absent consistently with existing runtime projection conventions.
- Failures: failed HTTP responses, Telegram `ok: false`, parse failures, timeouts, conflict/retry callbacks, reconnect attempts, and verifier failures do not clear or advance the last successful value.
- Restart: no synthetic timestamp is written at startup. The previous persisted value remains historical evidence until a real successful current-generation dedicated `getUpdates` response occurs.
- Generation: a late completion from an older generation cannot advance the timestamp because `_record_polling_progress()` already rejects mismatched generations.

## Evidence

Evidence is tracked in `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/research/evidence-log.csv` and `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/research/source-register.csv`.

## Open Questions And Risks

- Implementation must choose whether the status field is absent until first success or explicitly `null`; the plan should keep existing status-file conventions.
- API projection tests should pin both `/api/status` and `/health/detailed` so the two surfaces cannot drift.
- Error-redaction tests should cover runtime status and logs because Telegram error strings can contain `getUpdates` URLs or conflict details.
