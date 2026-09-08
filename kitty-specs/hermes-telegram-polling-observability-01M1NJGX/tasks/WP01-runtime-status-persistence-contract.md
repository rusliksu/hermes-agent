---
work_package_id: WP01
title: Runtime Status Persistence Contract
dependencies: []
requirement_refs:
- FR-001
- FR-006
- FR-007
planning_base_branch: codex/hermes-telegram-polling-planning-root
merge_target_branch: codex/hermes-telegram-polling-planning-root
branch_strategy: Planning artifacts for this mission were generated on codex/hermes-telegram-polling-planning-root. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into codex/hermes-telegram-polling-planning-root unless the human explicitly redirects the landing branch.
subtasks:
- T001
- T002
- T003
- T004
phase: Phase 1 - Foundation
agent: codex-wp01-gate-review
history:
- at: '2026-09-04T19:49:03Z'
  actor: system
  action: Prompt generated via /spec-kitty.tasks
agent_profile: python-pedro
authoritative_surface: gateway/
create_intent: []
execution_mode: code_change
model: ''
owned_files:
- gateway/status.py
- tests/gateway/test_status.py
role: implementer
tags: []
task_type: implement
tracker_refs: []
---

# Work Package Prompt: WP01 - Runtime Status Persistence Contract

## ⚡ Do This First: Load Agent Profile

Use the `/ad-hoc-profile-load` skill to load the agent profile specified in the frontmatter, and behave according to its guidance before parsing the rest of this prompt.

- **Profile**: `python-pedro`
- **Role**: `implementer`
- **Agent/tool**: `codex`

If the profile cannot be loaded, continue with Python implementation discipline and note the issue in the Activity Log.

## Objectives & Success Criteria

- Add an optional `last_successful_poll_at` platform status value to the existing runtime status write contract.
- Preserve current `write_runtime_status()` semantics for gateway state, platform state, error fields, active agent counts, and served profiles.
- Add behavior-contract tests proving old records without the field remain valid and timestamp writes do not clear unrelated platform fields.

## Context & Constraints

- Authoritative spec: `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/spec.md`.
- Architecture plan: `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/plan.md`.
- Contract: `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/contracts/status-projection.md`.
- Only own `gateway/status.py` and `tests/gateway/test_status.py`.
- No new endpoint, config schema, storage file, migration, or broad status abstraction.
- Do not persist Telegram payloads, IDs, tokens, URLs, query strings, or exception details.

## Branch Strategy

- **Strategy**: coordination branch planning; implementation worktree allocated by Spec Kitty after tasks finalization.
- **Planning base branch**: `main`
- **Merge target branch**: `main`

Execution worktrees are allocated from `lanes.json` by the official runtime. Do not implement from the root checkout unless Spec Kitty explicitly assigns that workspace.

## Subtasks & Detailed Guidance

### Subtask T001 - Extend runtime status timestamp write contract

- **Purpose**: Give platform callers a supported way to persist `last_successful_poll_at`.
- **Steps**:
  - Inspect `gateway/status.py::write_runtime_status()`.
  - Add a keyword-only optional parameter for the timestamp using the module's existing `_UNSET` pattern.
  - When `platform` is provided and the timestamp is not `_UNSET`, write it into that platform payload.
  - Keep `updated_at` behavior unchanged.
- **Files**: `gateway/status.py`.
- **Parallel?**: No; this defines the shared contract for later WPs.
- **Notes**: Do not validate Telegram semantics here. This module only persists the value it receives.

### Subtask T002 - Preserve compatibility and unrelated platform fields

- **Purpose**: Ensure the new field is additive.
- **Steps**:
  - Preserve existing platform payload values when only the timestamp is written.
  - Confirm explicit `None` behavior follows the same `_UNSET` contract as existing fields if a caller passes it.
  - Do not require older `gateway_state.json` files to contain the new field.
- **Files**: `gateway/status.py`.
- **Parallel?**: No.
- **Notes**: Avoid changing `parse_active_agents()` or drainability logic.

### Subtask T003 - Add runtime status unit coverage

- **Purpose**: Lock the persistence contract before adapter/projection WPs depend on it.
- **Steps**:
  - Add tests in `tests/gateway/test_status.py` for writing `last_successful_poll_at`.
  - Assert existing platform state/error fields survive timestamp writes.
  - Assert the value is stored exactly as a UTC ISO string supplied by the caller.
- **Files**: `tests/gateway/test_status.py`.
- **Parallel?**: Yes, after T001 shape is known.
- **Notes**: Prefer deterministic string values; do not depend on real wall-clock time in this WP.

### Subtask T004 - Verify older status files remain valid

- **Purpose**: Protect backward compatibility.
- **Steps**:
  - Add or extend a test that reads a runtime status record with no `last_successful_poll_at`.
  - Assert status helpers still parse and derive busy/drainable behavior normally.
  - Assert no migration or error is required.
- **Files**: `tests/gateway/test_status.py`.
- **Parallel?**: Yes.
- **Notes**: This test should not force all platform payloads to gain the field.

## Test Strategy

Run after implementation, before review:

```bash
pytest tests/gateway/test_status.py
```

## Risks & Mitigations

- **Risk**: Accidentally clearing `error_code` or `state`. Mitigate with preservation tests.
- **Risk**: Over-validating timestamp format in the persistence module. Mitigate by keeping semantic validation in adapter tests.

## Review Guidance

- Verify `write_runtime_status()` remains backward-compatible.
- Verify no new endpoint/config/schema/storage file was added.
- Verify tests assert behavior relationships rather than snapshot field counts.

## Activity Log

- 2026-09-04T19:49:03Z - system - Prompt created.
- 2026-09-06T12:35:21Z – codex:gpt-5:python-pedro:implementer – shell_pid=29571 – Assigned agent via action command
- 2026-09-06T12:51:15Z – codex:gpt-5:python-pedro:implementer – shell_pid=29571 – Moved to for_review
- 2026-09-06T14:20:26Z – codex-wp01-gate-review – shell_pid=112799 – Started review via action command
- 2026-09-06T14:23:52Z – codex-wp01-gate-review – shell_pid=112799 – Rerun pre-review gate after verified WP01 baseline placement
- 2026-09-06T14:24:06Z – codex-wp01-gate-review – shell_pid=116568 – Started review via action command
