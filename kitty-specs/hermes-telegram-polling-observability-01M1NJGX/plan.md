# Implementation Plan: Persist Telegram Polling Success Timestamp

**Branch**: `kitty/mission-hermes-telegram-polling-observability-01M1NJGX` | **Date**: 2026-09-04 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/spec.md`

## Summary

Persist `last_successful_poll_at` for Telegram when the existing current-generation dedicated `getUpdates` success path records polling progress. Surface the value through existing runtime status projections, keep older status files compatible, and add focused regression coverage for success, failure, stale generation, projection, restart/absence, and redaction behavior.

## Technical Context

**Language/Version**: Python 3.x as used by the Hermes gateway/runtime.
**Primary Dependencies**: Existing Telegram platform adapter, existing gateway runtime status module, existing dashboard/API server status handlers.
**Storage**: Existing local `gateway_state.json` runtime status file; no migration and no new persistent store.
**Testing**: Focused pytest coverage for Telegram polling progress, network reconnect/stale generation behavior, runtime status persistence, API server `/health/detailed`, dashboard `/api/status`, and Telegram error redaction.
**Target Platform**: Hermes local gateway and dashboard runtime.
**Project Type**: Python gateway/plugin status enhancement with documentation.
**Performance Goals**: No blocking network calls or status reads; at most one additional best-effort runtime-status write per accepted successful polling progress event.
**Constraints**: No new endpoint, no new configuration schema, no change to polling/retry/reconnect/verifier behavior, no Telegram payload/ID/token/URL/query exposure.
**Scale/Scope**: One platform-specific observability field plus existing status projections and documentation.

## Charter Check

Pre-design:

- PASS: Keeps core narrow by extending existing adapter and runtime status surfaces.
- PASS: Preserves cache/tool footprint; no model tool or prompt-surface change.
- PASS: Keeps privacy boundary explicit; only a UTC timestamp is persisted.
- PASS: Plans behavior-contract tests rather than snapshot counts.
- PASS: No live restart, deployment, push, PR, merge, cleanup, secret handling, or global setup change required.

Post-design:

- PASS: Design remains additive and backward-compatible.
- PASS: Work is decomposed into reviewable local concerns with no unrelated refactor.
- PASS: Documentation duty is included because observable operator behavior changes.

## Project Structure

### Documentation

```
kitty-specs/hermes-telegram-polling-observability-01M1NJGX/
├── plan.md
├── spec.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── status-projection.md
└── tasks/
```

### Source Code

```
plugins/platforms/telegram/adapter.py
gateway/status.py
hermes_cli/web_server.py
gateway/platforms/api_server.py
tests/gateway/test_telegram_polling_progress.py
tests/test_telegram_polling_progress_ptb.py
tests/gateway/test_telegram_network_reconnect.py
tests/gateway/test_status.py
tests/gateway/test_api_server.py
tests/hermes_cli/test_web_server.py
tests/gateway/test_telegram_error_redaction.py
docs/
```

**Structure Decision**: Extend the existing Telegram polling observation path and runtime status projection path in place. Do not introduce a new service, endpoint, config key, database, or broad status abstraction.

## Complexity Tracking

No charter violations require justification.

## Implementation Concern Map

### IC-01 — Runtime Status Field Contract

- **Purpose**: Add the optional `last_successful_poll_at` platform field to the existing runtime status persistence contract.
- **Relevant requirements**: FR-001, FR-006, FR-007, NFR-001, NFR-004, C-002.
- **Affected surfaces**: `gateway/status.py`, `tests/gateway/test_status.py`.
- **Sequencing/depends-on**: none.
- **Risks**: Must preserve existing update semantics so unrelated platform fields are not cleared.

### IC-02 — Telegram Polling Progress Write

- **Purpose**: Persist the UTC timestamp only after the existing current-generation dedicated `getUpdates` success gates accept progress.
- **Relevant requirements**: FR-001, FR-002, FR-003, FR-004, FR-005, NFR-002, NFR-003, C-001, C-003.
- **Affected surfaces**: `plugins/platforms/telegram/adapter.py`, `tests/gateway/test_telegram_polling_progress.py`, `tests/test_telegram_polling_progress_ptb.py`, `tests/gateway/test_telegram_network_reconnect.py`.
- **Sequencing/depends-on**: IC-01.
- **Risks**: A timestamp written before generation checks would let stale completions falsely heal current status.

### IC-03 — Existing Status Projections

- **Purpose**: Ensure `/api/status` and `/health/detailed` expose the same persisted timestamp through existing platform status payloads.
- **Relevant requirements**: FR-006, FR-007, NFR-004, C-002.
- **Affected surfaces**: `hermes_cli/web_server.py`, `gateway/platforms/api_server.py`, `tests/hermes_cli/test_web_server.py`, `tests/gateway/test_api_server.py`.
- **Sequencing/depends-on**: IC-01.
- **Risks**: Projection code must not create a new public data leak or drift between dashboard and gateway health responses.

### IC-04 — Documentation And Privacy Verification

- **Purpose**: Document operator semantics and verify status/log redaction for the new field.
- **Relevant requirements**: FR-008, NFR-003, NFR-005.
- **Affected surfaces**: `docs/`, `tests/gateway/test_telegram_error_redaction.py`.
- **Sequencing/depends-on**: IC-02 and IC-03.
- **Risks**: Documentation must distinguish polling progress from gateway `updated_at` and general Telegram API health.

## Test Plan

- Extend polling progress tests to assert successful current-generation `getUpdates` responses with empty and non-empty results advance `last_successful_poll_at`.
- Extend polling progress tests to assert HTTP errors, Telegram API errors, parse failures, timeouts, retry failures, `getMe`, `start_polling`, and non-`getUpdates` activity do not advance the field.
- Extend stale-generation/reconnect tests to prove old-generation completions cannot advance the timestamp after a newer generation begins.
- Extend status persistence tests to cover absent older records, explicit timestamp writes, unrelated field preservation, and UTC ISO formatting.
- Extend `/api/status` and `/health/detailed` tests to prove both surfaces project the same platform timestamp without adding a new endpoint.
- Extend Telegram error-redaction tests to prove no payload, IDs, tokens, URLs, query strings, or error details are introduced in status/log surfaces.

## Implementation Notes

- Use an injectable or easily frozen UTC time source at the narrow write boundary so tests do not depend on wall-clock timing.
- Prefer the adapter's existing `_write_runtime_status_safe(...)` helper so status persistence remains best-effort.
- Do not clear the timestamp on failures; absence or staleness is interpreted by consumers, not synthesized by failure paths.
- Run focused tests after implementation and before any review or accept step.

## Open Questions

None. Routine implementation choices are bounded by the spec, research, and constraints above.
