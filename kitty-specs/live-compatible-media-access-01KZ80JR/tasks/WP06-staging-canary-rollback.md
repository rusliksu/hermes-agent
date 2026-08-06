---
work_package_id: WP06
title: Staging canary, rollback and live gate packet
dependencies:
- WP01
- WP02
- WP03
- WP04
requirement_refs:
- FR-010
tracker_refs: []
planning_base_branch: codex/live-compatible-media-cutover
merge_target_branch: codex/live-compatible-media-cutover
branch_strategy: Planning artifacts for this mission were generated on codex/live-compatible-media-cutover. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into codex/live-compatible-media-cutover unless the human explicitly redirects the landing branch.
subtasks:
- T019
- T020
- T021
- T022
phase: Phase 6 - Staging and rollout gate
assignee: ''
agent: codex
history:
- at: '2026-08-05T04:00:00Z'
  actor: system
  action: Prompt generated via spec-kitty.tasks
agent_profile: reviewer-renata
authoritative_surface: docs/ops/
create_intent:
- docs/ops/media-access-canary-rollback.md
- tests/test_media_access_canary.py
execution_mode: planning_artifact
model: ''
owned_files:
- docs/ops/media-access-canary-rollback.md
- tests/test_media_access_canary.py
role: release reviewer
tags: []
task_type: implement
---

# Work Package Prompt: WP06 -- Staging canary, rollback and live gate packet

## Objectives & Success Criteria

- Build and verify a candidate from live-derived HEAD, not the divergent experimental branch.
- Produce redacted evidence, rollback references and a separate live approval checklist.

## Context & Constraints

- Depends on WP01--WP05.
- No live config apply, service restart, Telegram canary, credential rotation or destructive migration in this package without a new explicit gate.

## Subtasks & Detailed Guidance

### T019 -- Candidate evidence

- Record base/head, changed-file manifest and SHA-256 hashes; redact all auth/provider values.

### T020 -- Synthetic canary

- Exercise owner, Юля, мама, other family, two rooms, unknown/malformed, guessed IDs, media chains and dashboard loopback health.

### T021 -- Rollback packet

- Document backup metadata, exact prior code/config references, restore sequence and stop conditions.

### T022 -- Live gate

- Stop after staging evidence. Only a later explicit user approval authorizes live apply/restart and Telegram canaries.

## Test Strategy

```bash
python -m pytest -q tests/test_media_access_canary.py
```

## Review Guidance

Review changed-file scope against live base, service health before/after, no restart loop, and that rollback is non-destructive.

## Activity Log

- 2026-08-05T04:00:00Z -- system -- Prompt created.
