# Data Model: Telegram Polling Success Timestamp

## Entity: Telegram Polling Progress Observation

Represents one observed result from the dedicated PTB `getUpdates` request path.

Attributes:

- `generation`: integer polling generation captured when the request was instrumented.
- `status_code`: HTTP response status observed by the request wrapper.
- `envelope_ok`: boolean derived from the parsed Telegram response envelope.
- `has_result`: boolean indicating the envelope contains a `result` key.
- `observed_at_utc`: timezone-aware UTC timestamp generated only after the observation passes success and generation gates.

Rules:

- Valid when `generation` is non-null, status is `2xx`, the payload parses, `ok is True`, and `result` exists.
- Empty `result` is still valid.
- Invalid observations are ignored and do not mutate persisted status.

## Entity: Runtime Status Platform Payload

Existing per-platform entry under `gateway_state.json["platforms"][platform]`.

New attribute:

- `last_successful_poll_at`: optional timezone-aware UTC ISO-8601 string for the last successful current-generation dedicated Telegram `getUpdates` response.

Existing related attributes:

- `state`: platform lifecycle state.
- `error_code`: platform error classifier.
- `error_message`: redacted platform error message.
- `updated_at`: runtime-status write timestamp.

Rules:

- Written only for `platform="telegram"` through `write_runtime_status()`.
- Omitted or absent remains valid for older status files and for platforms that do not report polling progress.
- A successful write updates the platform entry without clearing unrelated state/error fields unless existing call semantics explicitly do so.

## Entity: API Status Projection

Read-only representation of runtime status returned by existing status endpoints.

Surfaces:

- `hermes_cli/web_server.py` `/api/status`: projects `gateway_platforms` from the runtime status file.
- `gateway/platforms/api_server.py` `/health/detailed`: projects `platforms` from the runtime status file.

Rules:

- No new endpoint.
- No new configuration schema.
- No Telegram payload, update count, Telegram ID, token, URL, query string, or exception text is exposed.

## Relationships

- A `Telegram Polling Progress Observation` may update one `Runtime Status Platform Payload` when and only when the observation is successful and belongs to the current generation.
- Both API status projections read the same runtime status platform payload, so they should expose the same timestamp value.
- Runtime restarts preserve previously persisted status history until the next real successful current-generation observation overwrites it.
