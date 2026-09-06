# Work Packages: Persist Telegram Polling Success Timestamp

**Inputs**: Design documents from `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/status-projection.md`, `quickstart.md`
**Tests**: Required by mission scope after implementation and before review/accept.
**Organization**: Fine-grained subtasks such as `T001` roll up into work packages such as `WP01`. Each work package is independently reviewable and keeps owned files non-overlapping.

---

## Work Package WP01: Runtime Status Persistence Contract (Priority: P0)

**Goal**: Add the optional timestamp field to the runtime status write contract while preserving existing platform status semantics.
**Independent Test**: Runtime status unit tests prove timestamp writes, absent older records, UTC formatting, and unrelated platform fields behave correctly.
**Prompt**: `tasks/WP01-runtime-status-persistence-contract.md`
**Requirement Refs**: FR-001, FR-006, FR-007, NFR-001, NFR-004, C-002

### Included Subtasks

- [x] T001 Extend `gateway/status.py` to accept and persist an optional Telegram polling timestamp field.
- [x] T002 Preserve backward compatibility and unrelated per-platform status fields during timestamp writes.
- [x] T003 Add focused `tests/gateway/test_status.py` coverage for timestamp persistence, absence, and UTC formatting.
- [x] T004 Verify the runtime status contract still treats older files without the field as valid.

### Implementation Notes

- Keep the field optional and platform-scoped.
- Do not add a new status file, database, migration, endpoint, or config key.
- Prefer the smallest change to the existing `write_runtime_status()` call shape.

### Parallel Opportunities

- WP01 is foundational. It should land before WP02 and WP03.

### Dependencies

- None.

### Risks & Mitigations

- Risk: overwriting existing platform state or errors. Mitigate with tests that write timestamp data alongside existing state/error fields.

---

## Work Package WP02: Telegram Polling Progress Recording (Priority: P0)

**Goal**: Persist `last_successful_poll_at` only after the existing current-generation dedicated `getUpdates` success gates accept polling progress.
**Independent Test**: Telegram polling tests prove successful empty/non-empty responses advance the timestamp and failure, non-polling, retry, and stale-generation paths do not.
**Prompt**: `tasks/WP02-telegram-polling-progress-recording.md`
**Requirement Refs**: FR-001, FR-002, FR-003, FR-004, FR-005, NFR-002, NFR-003, C-001, C-003

### Included Subtasks

- [ ] T005 Update `plugins/platforms/telegram/adapter.py` to write the timestamp after current-generation progress is accepted.
- [ ] T006 Add success-path tests for empty and non-empty `getUpdates` results.
- [ ] T007 Add no-advance tests for HTTP/API errors, parse failures, timeouts, retry failures, `getMe`, `start_polling`, and non-`getUpdates` activity.
- [ ] T008 Add or extend stale-generation and network reconnect tests so old completions cannot advance the timestamp.

### Implementation Notes

- Reuse the adapter's existing best-effort runtime status helper.
- Generate the timestamp only after `_record_polling_progress()` passes teardown, accepting-state, and generation checks.
- Do not alter polling, retry, reconnect, conflict, verifier, or send-path behavior.

### Parallel Opportunities

- Success/failure test drafting can proceed after WP01 defines the status write contract.

### Dependencies

- Depends on WP01.

### Risks & Mitigations

- Risk: a stale generation completion falsely updates status. Mitigate with explicit stale-generation regression coverage.

---

## Work Package WP03: Status Projections, Docs, And Redaction (Priority: P1)

**Goal**: Ensure existing status surfaces project the timestamp consistently, document operator semantics, and verify the privacy boundary.
**Independent Test**: Dashboard/API status tests expose the same timestamp, and redaction tests prove no Telegram-sensitive data is introduced.
**Prompt**: `tasks/WP03-status-projections-docs-and-redaction.md`
**Requirement Refs**: FR-006, FR-007, FR-008, NFR-003, NFR-004, NFR-005, C-002, C-004

### Included Subtasks

- [x] T009 Verify whether existing `/api/status` and `/health/detailed` projections already expose the new platform field; adjust only if required.
- [x] T010 Add `/api/status` coverage for Telegram `last_successful_poll_at` projection and absent-field compatibility.
- [x] T011 Add `/health/detailed` coverage for Telegram `last_successful_poll_at` projection and parity with `/api/status`.
- [x] T012 Update product documentation and error-redaction tests for timestamp semantics and sensitive-data exclusion.

### Implementation Notes

- Do not create a new endpoint.
- Keep public status exposure limited to the timestamp field already present in runtime status.
- Documentation should distinguish this value from gateway `updated_at` and general Telegram API health.

### Parallel Opportunities

- Documentation can draft after WP02 names the final status semantics, but projection tests depend on WP01.

### Dependencies

- Depends on WP01.
- Depends on WP02.

### Risks & Mitigations

- Risk: projection work accidentally leaks platform internals on public status. Mitigate with explicit redaction assertions.

---

## Dependency & Execution Summary

- **Sequence**: WP01 -> WP02 -> WP03.
- **Parallelization**: After WP01, WP02 implementation and parts of WP03 projection test drafting can proceed, but WP03 final docs/redaction should wait for WP02 semantics.
- **MVP Scope**: WP01 plus WP02 produces the persisted timestamp; WP03 completes user-visible projection validation and documentation.
- **Review Gate**: Source inspection of affected paths is required before approval. Focused tests must run after implementation and before review or accept.

---

## Requirements Coverage Summary

| Requirement ID | Covered By Work Package(s) |
| --- | --- |
| FR-001 | WP01, WP02 |
| FR-002 | WP02 |
| FR-003 | WP02 |
| FR-004 | WP02 |
| FR-005 | WP02 |
| FR-006 | WP01, WP03 |
| FR-007 | WP01, WP03 |
| FR-008 | WP03 |
| NFR-001 | WP01 |
| NFR-002 | WP02 |
| NFR-003 | WP02, WP03 |
| NFR-004 | WP01, WP03 |
| NFR-005 | WP03 |
| C-001 | WP02 |
| C-002 | WP01, WP03 |
| C-003 | WP02 |
| C-004 | WP03 |

---

## Subtask Index (Reference)

| Subtask ID | Summary | Work Package | Priority | Parallel? |
| --- | --- | --- | --- | --- |
| T001 | Extend runtime status timestamp write contract | WP01 | P0 | No |
| T002 | Preserve compatibility and unrelated platform fields | WP01 | P0 | No |
| T003 | Add runtime status unit coverage | WP01 | P0 | Yes |
| T004 | Verify older status files remain valid | WP01 | P0 | Yes |
| T005 | Persist timestamp from accepted polling progress | WP02 | P0 | No |
| T006 | Cover successful empty/non-empty getUpdates results | WP02 | P0 | Yes |
| T007 | Cover failures and non-polling no-advance cases | WP02 | P0 | Yes |
| T008 | Cover stale generation and reconnect no-advance cases | WP02 | P0 | Yes |
| T009 | Verify/adjust existing status projections | WP03 | P1 | No |
| T010 | Add dashboard /api/status projection coverage | WP03 | P1 | Yes |
| T011 | Add /health/detailed projection parity coverage | WP03 | P1 | Yes |
| T012 | Update docs and redaction coverage | WP03 | P1 | Yes |
