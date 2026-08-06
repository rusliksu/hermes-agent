---
work_package_id: WP01
title: Six-field access contract and fail-closed resolver
dependencies: []
requirement_refs:
- FR-001
- FR-002
- FR-004
- FR-007
tracker_refs: []
planning_base_branch: codex/live-compatible-media-cutover
merge_target_branch: codex/live-compatible-media-cutover
branch_strategy: Planning artifacts for this mission were generated on codex/live-compatible-media-cutover. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into codex/live-compatible-media-cutover unless the human explicitly redirects the landing branch.
subtasks:
- T001
- T002
- T003
- T004
phase: Phase 1 - Contract
assignee: ''
agent: codex
history:
- at: '2026-08-05T04:00:00Z'
  actor: system
  action: Prompt generated via spec-kitty.tasks
agent_profile: implementer-ivan
authoritative_surface: gateway/
create_intent:
- gateway/access_registry.py
- tests/gateway/test_access_registry.py
- tests/gateway/test_profile_routing_fail_closed.py
execution_mode: code_change
model: ''
owned_files:
- gateway/access_registry.py
- gateway/profile_routing.py
- gateway/authz_mixin.py
- tests/gateway/test_access_registry.py
- tests/gateway/test_profile_routing_fail_closed.py
role: implementer
tags: []
task_type: implement
---

# Work Package Prompt: WP01 -- Six-field access contract and fail-closed resolver

## Objectives & Success Criteria

- Implement `ResolvedAccessContext` with exactly six serialized fields: `principal_id`, `role_id`, `profile_id`, `conversation_scope`, `capabilities`, `delivery_target`.
- Resolve confirmed DM/room identities to owner, family_standard, family_sandbox or shared_room, or deny before model/session/tools.
- Do not use username/display name, active profile or owner fallback as identity.
- Keep all deny/audit data redacted.

## Context & Constraints

- Start from live-derived base `a4096896ed92d1edb3dd02e62876dc0fc1ce140a`.
- Read `kitty-specs/live-compatible-media-access-01KZ80JR/spec.md` and `plan.md` first.
- Keep registry pure stdlib; do not import provider SDKs, dashboard or credential files.
- Preserve legacy mode behind an explicit gate; malformed new policy is deny.

## Subtasks & Detailed Guidance

### T001 -- Contract

- Add immutable dataclasses/types and strict serializer/deserializer. Reject extra/missing fields.
- Define role policies and effective capability intersection without embedding user data in role prompts.

### T002 -- Routes

- Extend route matching to include exact `platform + account + peer_kind + user_id`.
- For Telegram DMs require normalized positive `user_id == chat_id`; rooms/topics require explicit binding and membership.
- Detect duplicate/ambiguous routes and reject them.

### T003 -- Ingress bridge

- Call resolver once at ingress and make deny a terminal result before model/session/tools.
- Remove error/failure paths that return active/default `HERMES_HOME` for rejected requests.

### T004 -- Tests

- Cover Руслан(owner), Юля(family_sandbox), мама and other family(family_standard), two rooms(shared_room), unknown, malformed, mismatched Telegram IDs and missing profile.
- Assert no model/session/tool callback is invoked after deny.

## Test Strategy

```bash
python -m pytest -q tests/gateway/test_access_registry.py tests/gateway/test_profile_routing_fail_closed.py
```

## Risks & Mitigations

- Missing adapter metadata: fail closed.
- Route/profile typo: fail closed with redacted route name only.

## Review Guidance

Check exact six-field shape, no default/owner fallback, no display-name identity and no credential reads.

## Activity Log

- 2026-08-05T04:00:00Z -- system -- Prompt created.
