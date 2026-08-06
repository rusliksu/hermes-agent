---
work_package_id: WP04
title: Policy validation, dry-run and dashboard audit surface
dependencies:
- WP01
requirement_refs:
- FR-004
- FR-006
- FR-008
tracker_refs: []
planning_base_branch: codex/live-compatible-media-cutover
merge_target_branch: codex/live-compatible-media-cutover
branch_strategy: Planning artifacts for this mission were generated on codex/live-compatible-media-cutover. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into codex/live-compatible-media-cutover unless the human explicitly redirects the landing branch.
subtasks:
- T013
- T014
- T015
phase: Phase 4 - Policy and audit
assignee: ''
agent: codex
history:
- at: '2026-08-05T04:00:00Z'
  actor: system
  action: Prompt generated via spec-kitty.tasks
agent_profile: implementer-ivan
authoritative_surface: hermes_cli/
create_intent:
- tests/hermes_cli/test_config_access_policy.py
execution_mode: code_change
model: ''
owned_files:
- hermes_cli/config.py
- hermes_cli/subcommands/config.py
- hermes_cli/dashboard_auth/audit.py
- tests/hermes_cli/test_config_access_policy.py
role: implementer
tags: []
task_type: implement
---

# Work Package Prompt: WP04 -- Policy validation, dry-run and dashboard audit surface

## Objectives & Success Criteria

- Validate policy shapes and render redacted effective rights without secrets or session contents.
- Add the existing dashboard's Access/Users surface and enforce reason-bound read-only break-glass lease.

## Context & Constraints

- Depends on WP01 and WP03.
- Keep dashboard loopback/SSH boundary; do not open an external listener.
- Lease never grants Telegram/model permissions, bulk search or export.

## Subtasks & Detailed Guidance

### T013 -- Config dry-run

- Add strict parse/check/dry-run for role, principal, room and media policy; unknown or malformed entries deny.
- Keep legacy config readable but do not broaden effective rights implicitly.

### T014 -- Dashboard integration

- Add list/role/profile health/redacted isolation status and role preview/change with confirmation/audit.
- Do not render private history in the dashboard flow.

### T015 -- Lease tests

- Require non-empty reason and second confirmation; enforce 15-minute expiry, manual revoke and metadata-only audit.

## Test Strategy

```bash
python -m pytest -q tests/hermes_cli/test_config_access_policy.py tests/hermes_cli/test_dashboard_admin_endpoints.py
```

## Risks & Mitigations

Treat a malformed policy as deny and retain previous valid policy only when runtime explicitly documents last-known-good behavior.

## Review Guidance

Confirm the dashboard cannot make a foreign profile visible to model/tool execution and audit contains no message text.

## Activity Log

- 2026-08-05T04:00:00Z -- system -- Prompt created.
