---
work_package_id: WP05
title: Migration and profile/room fixture tooling
dependencies:
- WP01
requirement_refs:
- FR-003
- FR-009
tracker_refs: []
planning_base_branch: codex/live-compatible-media-cutover
merge_target_branch: codex/live-compatible-media-cutover
branch_strategy: Planning artifacts for this mission were generated on codex/live-compatible-media-cutover. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into codex/live-compatible-media-cutover unless the human explicitly redirects the landing branch.
subtasks:
- T016
- T017
- T018
phase: Phase 5 - Migration safety
assignee: ''
agent: codex
history:
- at: '2026-08-05T04:00:00Z'
  actor: system
  action: Prompt generated via spec-kitty.tasks
agent_profile: implementer-ivan
authoritative_surface: tests/fixtures/
create_intent:
- tests/fixtures/access_policy_matrix.json
- tests/fixtures/migration_fixture.json
- tests/test_profile_migration.py
execution_mode: code_change
model: ''
owned_files:
- tests/fixtures/access_policy_matrix.json
- tests/fixtures/migration_fixture.json
- tests/test_profile_migration.py
role: implementer
tags: []
task_type: implement
---

# Work Package Prompt: WP05 -- Migration and profile/room fixture tooling

## Objectives & Success Criteria

- Produce dry-run counts/hashes and fixtures for nine family principals plus two shared rooms.
- Migrate only unambiguous DM sessions while preserving IDs/timestamps; keep ambiguous/global memory outside family profiles.

## Context & Constraints

- Depends on WP01 and WP02.
- Synthetic/redacted data only; never read credential/auth files or global personal memory contents.
- Ambiguous records are closed read-only legacy archive, not model-visible.

## Subtasks & Detailed Guidance

### T016 -- Planner/fixtures

- Add deterministic ownership and room fixtures with opaque principal IDs and redacted hashes.
- Preserve existing family role labels for compatibility, but assert that every private family binding receives the same safe capability set; Руслан remains `owner`.

### T017 -- Migration rules

- Preserve session IDs/timestamps for exact principal-owned DMs.
- Never infer identity from username/display name; classify uncertain rows as legacy.

### T018 -- Tests

- Assert count/hash parity, no global `MEMORY.md`/`USER.md` copy and no ambiguous record in profile namespaces.

## Test Strategy

```bash
python -m pytest -q tests/test_profile_migration.py
```

## Review Guidance

Check that migration is dry-run/reversible and has no destructive cleanup operation.

## Activity Log

- 2026-08-05T04:00:00Z -- system -- Prompt created.
