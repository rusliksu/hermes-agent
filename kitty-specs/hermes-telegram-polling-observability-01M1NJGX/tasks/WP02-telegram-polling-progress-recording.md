---
work_package_id: WP02
title: Telegram Polling Progress Recording
dependencies:
- WP01
requirement_refs:
- FR-001
- FR-002
- FR-003
- FR-004
- FR-005
tracker_refs: []
planning_base_branch: main
merge_target_branch: main
branch_strategy: Planning artifacts for this mission were generated on main. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into main unless the human explicitly redirects the landing branch.
subtasks:
- T005
- T006
- T007
- T008
phase: Phase 2 - Adapter Behavior
assignee: ''
agent: codex
history:
- at: '2026-09-04T19:49:03Z'
  actor: system
  action: Prompt generated via /spec-kitty.tasks
agent_profile: python-pedro
authoritative_surface: plugins/platforms/telegram/
create_intent: []
execution_mode: code_change
model: ''
owned_files:
- plugins/platforms/telegram/adapter.py
- tests/gateway/test_telegram_polling_progress.py
- tests/test_telegram_polling_progress_ptb.py
- tests/gateway/test_telegram_network_reconnect.py
role: implementer
tags: []
task_type: implement
---

# Work Package Prompt: WP02 - Telegram Polling Progress Recording

## ⚡ Do This First: Load Agent Profile

Use the `/ad-hoc-profile-load` skill to load the agent profile specified in the frontmatter, and behave according to its guidance before parsing the rest of this prompt.

- **Profile**: `python-pedro`
- **Role**: `implementer`
- **Agent/tool**: `codex`

If the profile cannot be loaded, continue with Python implementation discipline and note the issue in the Activity Log.

## Objectives & Success Criteria

- Persist `last_successful_poll_at` only when current-generation dedicated Telegram `getUpdates` progress is accepted.
- Preserve existing polling, retry, reconnect, conflict, verifier, and send-path behavior.
- Add focused tests for success, failure, non-polling, timeout/retry, and stale-generation cases.

## Context & Constraints

- Depends on WP01's runtime status write contract.
- Source evidence: `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/research.md`.
- Only own the Telegram adapter and listed Telegram polling/reconnect tests.
- No payload, Telegram ID, bot token, request URL, query string, update count, or exception detail may be persisted.

## Branch Strategy

- **Strategy**: coordination branch planning; implementation worktree allocated by Spec Kitty after tasks finalization.
- **Planning base branch**: `main`
- **Merge target branch**: `main`

Execution worktrees are allocated from `lanes.json` by the official runtime. Start only when Spec Kitty assigns WP02 and its dependencies are satisfied.

## Subtasks & Detailed Guidance

### Subtask T005 - Persist timestamp from accepted polling progress

- **Purpose**: Make the existing accepted progress point update runtime status.
- **Steps**:
  - Inspect `_begin_polling_generation()`, `_record_polling_progress()`, `_observe_polling_request_result()`, and `_instrument_polling_request()`.
  - Generate a UTC ISO timestamp only after `_record_polling_progress()` has accepted current-generation progress.
  - Write through the adapter's existing best-effort runtime status helper.
  - Do not write from `getMe`, `start_polling`, verifier success, reconnect setup, conflict handling, or general Bot API request paths.
- **Files**: `plugins/platforms/telegram/adapter.py`.
- **Parallel?**: No.
- **Notes**: The timestamp should reflect accepted observation time, not Telegram message time.

### Subtask T006 - Cover successful empty and non-empty getUpdates results

- **Purpose**: Lock the success semantics.
- **Steps**:
  - Extend existing polling progress tests to assert empty `result: []` advances `last_successful_poll_at`.
  - Add/extend a non-empty `result` success case and assert only the timestamp is persisted.
  - Freeze or inject time so assertions are deterministic.
- **Files**: `tests/gateway/test_telegram_polling_progress.py`, `tests/test_telegram_polling_progress_ptb.py`.
- **Parallel?**: Yes, after T005 establishes the injection/write shape.
- **Notes**: Do not assert incidental status field counts.

### Subtask T007 - Cover failure and non-polling no-advance cases

- **Purpose**: Prevent false health signals.
- **Steps**:
  - Assert HTTP errors and Telegram `ok: false` envelopes do not write.
  - Assert parse failures do not write.
  - Assert timeouts/retry failures do not write.
  - Assert `getMe`, `start_polling`, and non-`getUpdates` activity do not write.
- **Files**: `tests/gateway/test_telegram_polling_progress.py`, `tests/test_telegram_polling_progress_ptb.py`.
- **Parallel?**: Yes.
- **Notes**: Reuse existing request doubles where possible.

### Subtask T008 - Cover stale generation and reconnect behavior

- **Purpose**: Ensure a previous generation cannot heal current status.
- **Steps**:
  - Extend stale-generation tests so a late old-generation completion leaves the timestamp unchanged.
  - Extend network reconnect tests so reconnect/conflict handling does not advance the timestamp until a real new-generation success occurs.
  - Preserve current assertions around conflict/network counters.
- **Files**: `tests/gateway/test_telegram_polling_progress.py`, `tests/gateway/test_telegram_network_reconnect.py`.
- **Parallel?**: Yes.
- **Notes**: Keep assertions tied to generation behavior, not elapsed time.

## Test Strategy

Run after implementation, before review:

```bash
pytest tests/gateway/test_telegram_polling_progress.py
pytest tests/test_telegram_polling_progress_ptb.py
pytest tests/gateway/test_telegram_network_reconnect.py
```

## Risks & Mitigations

- **Risk**: Writing before generation checks. Mitigate with stale-generation tests.
- **Risk**: Coupling health to general Telegram API success. Mitigate with explicit non-`getUpdates` no-write tests.
- **Risk**: Exposing response content. Mitigate by persisting only caller-generated timestamp strings.

## Review Guidance

- Confirm `_record_polling_progress()` remains the only write point for successful polling progress.
- Confirm existing polling/reconnect behavior is otherwise unchanged.
- Confirm all focused tests were run after implementation.

## Activity Log

- 2026-09-04T19:49:03Z - system - Prompt created.
