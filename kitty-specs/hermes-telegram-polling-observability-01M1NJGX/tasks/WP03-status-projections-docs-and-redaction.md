---
work_package_id: WP03
title: Status Projections, Docs, And Redaction
dependencies:
- WP01
- WP02
requirement_refs:
- FR-006
- FR-007
- FR-008
tracker_refs: []
planning_base_branch: main
merge_target_branch: main
branch_strategy: Planning artifacts for this mission were generated on main. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into main unless the human explicitly redirects the landing branch.
subtasks:
- T009
- T010
- T011
- T012
phase: Phase 3 - Projection And Documentation
assignee: "codex-runtime-binding-review"
agent: "codex-runtime-binding-review"
shell_pid: "6141"
history:
- at: '2026-09-04T19:49:03Z'
  actor: system
  action: Prompt generated via /spec-kitty.tasks
agent_profile: python-pedro
authoritative_surface: hermes_cli/
create_intent: []
execution_mode: code_change
model: ''
owned_files:
- hermes_cli/web_server.py
- gateway/platforms/api_server.py
- tests/hermes_cli/test_web_server.py
- tests/gateway/test_api_server.py
- tests/gateway/test_telegram_error_redaction.py
- website/docs/user-guide/messaging/telegram.md
role: implementer
tags: []
task_type: implement
---

# Work Package Prompt: WP03 - Status Projections, Docs, And Redaction

## ⚡ Do This First: Load Agent Profile

Use the `/ad-hoc-profile-load` skill to load the agent profile specified in the frontmatter, and behave according to its guidance before parsing the rest of this prompt.

- **Profile**: `python-pedro`
- **Role**: `implementer`
- **Agent/tool**: `codex`

If the profile cannot be loaded, continue with Python implementation discipline and note the issue in the Activity Log.

## Objectives & Success Criteria

- Ensure existing status surfaces expose `last_successful_poll_at` consistently when runtime status contains it.
- Preserve backward compatibility when runtime status lacks the field.
- Document operator semantics and prove the new observability field does not leak Telegram-sensitive data.

## Context & Constraints

- Depends on WP01 and WP02.
- Contract: `kitty-specs/hermes-telegram-polling-observability-01M1NJGX/contracts/status-projection.md`.
- Only adjust projection code if existing pass-through behavior is insufficient.
- No new endpoint, no new config key, no live gateway traffic, no secrets.

## Branch Strategy

- **Strategy**: coordination branch planning; implementation worktree allocated by Spec Kitty after tasks finalization.
- **Planning base branch**: `main`
- **Merge target branch**: `main`

Execution worktrees are allocated from `lanes.json` by the official runtime. Start only when Spec Kitty assigns WP03 and dependencies are satisfied.

## Subtasks & Detailed Guidance

### Subtask T009 - Verify or adjust existing status projections

- **Purpose**: Keep the timestamp visible through the existing status contract.
- **Steps**:
  - Inspect `/api/status` handling in `hermes_cli/web_server.py`.
  - Inspect `/health/detailed` handling in `gateway/platforms/api_server.py`.
  - If existing runtime-platform pass-through already exposes the field, leave production code unchanged and add tests only.
  - If projection filtering drops the field, make the smallest projection adjustment required.
- **Files**: `hermes_cli/web_server.py`, `gateway/platforms/api_server.py`.
- **Parallel?**: No.
- **Notes**: Do not expose host paths, PIDs, URLs, or other gated details.

### Subtask T010 - Add /api/status projection coverage

- **Purpose**: Prove the dashboard-facing status readout carries the Telegram timestamp.
- **Steps**:
  - Add focused tests in `tests/hermes_cli/test_web_server.py`.
  - Assert `gateway_platforms.telegram.last_successful_poll_at` appears when runtime status contains it.
  - Assert older records without the field remain accepted.
- **Files**: `tests/hermes_cli/test_web_server.py`.
- **Parallel?**: Yes, after WP01 defines persisted shape.
- **Notes**: Keep tests scoped; do not snapshot the full `/api/status` response.

### Subtask T011 - Add /health/detailed projection parity coverage

- **Purpose**: Prove the API server detailed health readout matches the same runtime platform data.
- **Steps**:
  - Add focused tests in `tests/gateway/test_api_server.py`.
  - Assert `platforms.telegram.last_successful_poll_at` appears when runtime status contains it.
  - Assert behavior is compatible when the field is absent.
  - Where practical, assert parity with `/api/status` field naming and value.
- **Files**: `tests/gateway/test_api_server.py`.
- **Parallel?**: Yes.
- **Notes**: Preserve existing auth requirements for `/health/detailed`.

### Subtask T012 - Update docs and redaction coverage

- **Purpose**: Make the operator-facing meaning clear and protect sensitive-data boundaries.
- **Steps**:
  - Update `website/docs/user-guide/messaging/telegram.md` with the meaning of `last_successful_poll_at`.
  - Document absence, restart preservation, empty-result success, and failure/no-advance behavior.
  - Extend `tests/gateway/test_telegram_error_redaction.py` or nearby coverage so status/log surfaces do not include Telegram payloads, IDs, bot tokens, URLs, query strings, or exception details because of this field.
- **Files**: `website/docs/user-guide/messaging/telegram.md`, `tests/gateway/test_telegram_error_redaction.py`.
- **Parallel?**: Yes, after WP02 finalizes semantics.
- **Notes**: Documentation should not instruct users to add config or restart solely for this field.

## Test Strategy

Run after implementation, before review:

```bash
pytest tests/hermes_cli/test_web_server.py
pytest tests/gateway/test_api_server.py
pytest tests/gateway/test_telegram_error_redaction.py
```

## Risks & Mitigations

- **Risk**: Public status grows beyond intended data. Mitigate by testing only the timestamp appears.
- **Risk**: Docs imply general Telegram API health. Mitigate by explicitly contrasting with `updated_at`, `getMe`, and startup/reconnect activity.

## Review Guidance

- Confirm no new endpoint or config schema was added.
- Confirm projection tests cover both present and absent field behavior.
- Confirm documentation and tests preserve the privacy boundary.

## Activity Log

- 2026-09-04T19:49:03Z - system - Prompt created.
- 2026-09-04T20:49:52Z – user – Verification complete on task branch d2649a434: focused per-file pytest with pytest-asyncio passed status 97, polling 24, PTB 8, reconnect 42, API 204, redaction 8, web status projection 2; diff-scoped ruff passed. No prior WP03 reviewer verdict artifact found, leaving review-ready.
- 2026-09-05T09:22:11Z – codex-runtime-binding-review – shell_pid=3290214 – Started review via action command
- 2026-09-06T10:43:26Z – codex-runtime-binding-review – shell_pid=3290214 – Moved to planned
- 2026-09-06T10:43:43Z – codex-runtime-binding-review – shell_pid=3290214 – Moved to in_progress
- 2026-09-06T10:46:41Z – codex-runtime-binding-review – shell_pid=3290214 – Moved to for_review
- 2026-09-06T12:02:33Z – codex-runtime-binding-review – shell_pid=6141 – Started review via action command
- 2026-09-06T12:08:06Z – codex-runtime-binding-review – shell_pid=6141 – Moved to planned
