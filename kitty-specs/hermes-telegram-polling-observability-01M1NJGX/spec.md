# Specification: Persist Telegram Polling Success Timestamp

## Intent Summary

Hermes operators need a reliable status signal showing when the Telegram adapter last completed a successful current-generation dedicated polling request. The signal must distinguish real `getUpdates` progress from general Telegram API health, reconnect attempts, startup calls, stale polling generations, and failures. The feature is observability-only: it must not change polling behavior, add configuration, add endpoints, or expose Telegram-sensitive data.

## Primary Actors

- Hermes operator: checks local dashboard, CLI-adjacent status, or gateway health to understand whether Telegram polling is alive.
- Telegram platform adapter: observes dedicated polling progress and updates runtime status.
- Status consumer: reads existing status surfaces to display platform health.

## User Scenarios And Testing

### Scenario 1: Successful Empty Poll Is Visible

Given the Telegram adapter receives a valid current-generation `getUpdates` success response with an empty result list, when an operator checks existing gateway status, then the Telegram platform status includes the UTC timestamp of that successful poll.

### Scenario 2: Successful Poll With Updates Is Visible

Given the Telegram adapter receives a valid current-generation `getUpdates` success response containing updates, when status is read, then the timestamp advances without exposing update payloads, update counts, Telegram IDs, request URLs, query strings, or bot-token material.

### Scenario 3: Failures Do Not Advance Progress

Given HTTP errors, Telegram API `ok: false` responses, parse failures, timeouts, retry-loop failures, reconnect attempts, `getMe`, `start_polling`, or other non-polling API activity occurs, when status is read, then the last successful polling timestamp remains unchanged.

### Scenario 4: Stale Generations Cannot Heal Current Status

Given a previous polling generation completes after a newer generation has started, when that stale completion is observed, then status does not advance and current-generation health remains governed by the newer generation.

### Scenario 5: Restart Preserves Historical Evidence

Given a gateway restarts before any new successful Telegram poll completes, when status is read, then no synthetic startup timestamp is written and any previous persisted successful polling timestamp remains historical evidence until a real current-generation success overwrites it.

## Domain Language

- Canonical term: `last_successful_poll_at`
- Meaning: the UTC timestamp of the last successful current-generation dedicated Telegram `getUpdates` response.
- Avoided meanings: general Telegram API success, `getMe` health, polling startup, reconnect attempt, verifier success without polling I/O, message receive time, or gateway runtime `updated_at`.

## Functional Requirements

| ID | Requirement | Status |
| --- | --- | --- |
| FR-001 | The system SHALL persist a Telegram platform status field named `last_successful_poll_at` when a current-generation dedicated `getUpdates` response is valid and successful. | Approved |
| FR-002 | The system SHALL count valid Telegram API success responses with empty `result` lists as successful polling progress. | Approved |
| FR-003 | The system SHALL count valid Telegram API success responses with non-empty `result` lists as successful polling progress without persisting any response content. | Approved |
| FR-004 | The system SHALL NOT advance `last_successful_poll_at` for HTTP errors, Telegram API error envelopes, parse failures, timeouts, retry-loop failures, reconnect handling, startup calls, `getMe`, `start_polling`, or any non-`getUpdates` request. | Approved |
| FR-005 | The system SHALL NOT advance `last_successful_poll_at` for completions from stale polling generations or while polling progress is not being accepted. | Approved |
| FR-006 | Existing status consumers SHALL be able to read the timestamp through the current gateway status surfaces without a new endpoint. | Approved |
| FR-007 | Runtime status files created before this field existed SHALL remain readable and shall not require migration. | Approved |
| FR-008 | Product documentation SHALL describe the timestamp meaning, absence behavior, restart behavior, and privacy boundary. | Approved |

## Non-Functional Requirements

| ID | Requirement | Status |
| --- | --- | --- |
| NFR-001 | The persisted value SHALL be a timezone-aware UTC ISO-8601 timestamp generated at the accepted successful polling observation time. | Approved |
| NFR-002 | The new status update SHALL add no more than one additional status-file write per accepted successful polling progress event. | Approved |
| NFR-003 | The feature SHALL expose zero Telegram payload fields, zero Telegram identifiers, zero bot-token fragments, and zero request URLs or query strings in status responses and logs. | Approved |
| NFR-004 | Status reads SHALL remain backward compatible when the timestamp field is absent, null, or from an older runtime-status file. | Approved |
| NFR-005 | Tests SHALL cover successful polling, empty results, failures, stale generations, restart/absence behavior, both existing status projections, and redaction before implementation is submitted for review. | Approved |

## Constraints

| ID | Constraint | Status |
| --- | --- | --- |
| C-001 | The feature SHALL use the existing Telegram polling progress observation path as the source of truth for successful polling progress. | Approved |
| C-002 | The feature SHALL use the existing runtime status persistence and projection surfaces; it SHALL NOT add a new endpoint or configuration schema. | Approved |
| C-003 | The feature SHALL be observability-only and SHALL NOT change Telegram polling, retry, reconnect, conflict, or verifier behavior. | Approved |
| C-004 | Implementation SHALL be limited to the Telegram adapter, runtime status persistence/projection contracts, focused tests, and product documentation. | Approved |

## Key Entities

- Telegram polling progress observation: a successful current-generation dedicated `getUpdates` result accepted by the adapter.
- Runtime status platform payload: the persisted per-platform status object that can include `last_successful_poll_at`.
- Status projection: existing read-only views that expose runtime platform status to operators.

## Assumptions

- Absence of `last_successful_poll_at` means no successful Telegram polling progress has been recorded in the current readable runtime status file.
- A restart does not by itself prove polling progress and therefore must not synthesize a new timestamp.
- Historical persisted values are useful as evidence and should remain until overwritten by a later accepted success.

## Success Criteria

- SC-001: In focused tests, 100% of valid current-generation `getUpdates` success cases advance the timestamp, including empty result lists.
- SC-002: In focused tests, 100% of listed failure, non-polling, and stale-generation cases leave the timestamp unchanged.
- SC-003: Both existing status surfaces expose the same timestamp value when runtime status contains it.
- SC-004: Redaction tests show no Telegram payloads, identifiers, tokens, URLs, or query strings are introduced into status responses or logs.
- SC-005: Product documentation lets an operator distinguish this timestamp from gateway runtime `updated_at` and general Telegram API health without reading source code.
