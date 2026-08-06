---
work_package_id: WP02
title: Profile, session and background isolation
dependencies:
- WP01
requirement_refs:
- FR-003
- FR-005
- FR-007
tracker_refs: []
planning_base_branch: codex/live-compatible-media-cutover
merge_target_branch: codex/live-compatible-media-cutover
branch_strategy: Planning artifacts for this mission were generated on codex/live-compatible-media-cutover. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into codex/live-compatible-media-cutover unless the human explicitly redirects the landing branch.
subtasks:
- T005
- T006
- T007
- T008
phase: Phase 2 - Runtime isolation
assignee: ''
agent: codex
history:
- at: '2026-08-05T04:00:00Z'
  actor: system
  action: Prompt generated via spec-kitty.tasks
agent_profile: implementer-ivan
authoritative_surface: gateway/
create_intent:
- tests/test_profile_isolation_runtime.py
execution_mode: code_change
model: ''
owned_files:
- gateway/run.py
- gateway/session_context.py
- tools/session_search_tool.py
- tools/memory_tool.py
- tests/test_profile_isolation_runtime.py
role: implementer
tags: []
task_type: implement
---

# Work Package Prompt: WP02 -- Profile, session and background isolation

## Objectives & Success Criteria

- Bind one access context to each task and propagate it through profile home, session, memory, tools, callbacks, cron, compaction, reset, restart and delegation.
- Reject foreign or model-supplied namespaces and never fall back to global/default home after a deny.
- Prove pairwise privacy under concurrent turns.

## Context & Constraints

- Depends on WP01 and its six-field context.
- Preserve task-local `contextvars`; do not reintroduce process-global identity environment variables.
- Use synthetic profile roots and IDs only.

## Subtasks & Detailed Guidance

### T005 -- Context binding

- Bind/reset context at ingress and turn completion/cancellation, including executor/background handoffs.
- Make delivery callbacks derive target only from trusted context.

### T006 -- Fail-closed profile home

- Replace missing/invalid profile fallback with explicit deny when the new gate is enabled.
- Ensure rejected turns cannot open active profile sessions or files.

### T007 -- Tool namespace guards

- Make session search, memory search, attachments and filesystem roots derive namespace from context; ignore/deny model arguments that name another profile.

### T008 -- Negative tests

- Add pairwise guessed-ID/path probes and callbacks/cron/delegation/compaction/reset/restart/concurrency cases.

## Test Strategy

```bash
python -m pytest -q tests/test_profile_isolation_runtime.py tests/agent/test_context_refs_concurrent.py tests/agent/test_file_safety_cross_profile.py
```

## Risks & Mitigations

Use explicit context propagation in thread/executor bridges; clear context after cancellation to prevent reuse.

## Review Guidance

Look for any `get_active_profile_name()`, `get_hermes_home()` or `os.getenv("HERMES_SESSION_...")` path reachable after a rejected or unbound request.

## Activity Log

- 2026-08-05T04:00:00Z -- system -- Prompt created.
