---
work_package_id: WP03
title: Scoped image/STT/TTS provider facade
dependencies:
- WP01
requirement_refs:
- FR-006
- FR-007
tracker_refs: []
planning_base_branch: codex/live-compatible-media-cutover
merge_target_branch: codex/live-compatible-media-cutover
branch_strategy: Planning artifacts for this mission were generated on codex/live-compatible-media-cutover. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into codex/live-compatible-media-cutover unless the human explicitly redirects the landing branch.
subtasks:
- T009
- T010
- T011
- T012
phase: Phase 3 - Media policy
assignee: ''
agent: codex
history:
- at: '2026-08-05T04:00:00Z'
  actor: system
  action: Prompt generated via spec-kitty.tasks
agent_profile: implementer-ivan
authoritative_surface: tools/
create_intent:
- tools/media_provider_routing.py
- tests/test_media_provider_routing.py
execution_mode: code_change
model: ''
owned_files:
- tools/media_provider_routing.py
- tools/image_generation_tool.py
- tools/transcription_tools.py
- tools/tts_tool.py
- tests/test_media_provider_routing.py
role: implementer
tags: []
task_type: implement
---

# Work Package Prompt: WP03 -- Scoped image/STT/TTS provider facade

## Objectives & Success Criteria

- Add typed ordered policies and one-attempt provider execution for image, STT and TTS.
- Enforce capability and backend policy from WP01 context.
- Keep secret references opaque and logs/evidence redacted.

## Context & Constraints

- Depends on WP01; do not copy the divergent experimental branch wholesale.
- Existing provider implementations remain the adapters.
- Shared rooms receive no media provider unless explicitly configured server-side.

## Subtasks & Detailed Guidance

### T009 -- Routing facade

- Implement policy parsing, provider availability, retryable/permanent classification and normalized result.
- Default order: image `openai-codex → fal → openrouter`; STT `local → mistral → openai → elevenlabs`; TTS `edge → openai → elevenlabs`.

### T010 -- Tool integration

- Route existing tool entry points through the facade; preserve legacy path when policy is disabled.
- Never pass secret values to model arguments, command environment or audit payload.

### T011 -- Tests

- Use fake providers and sentinel secret refs; assert order, one attempt, permanent stop and zero secret leakage.

### T012 -- Legacy parity

- Prove current configured provider behavior is unchanged with compatibility policy off.

## Test Strategy

```bash
python -m pytest -q tests/test_media_provider_routing.py tests/agent/test_image_gen_registry.py tests/agent/test_tts_registry.py
```

## Risks & Mitigations

Normalize plugin result/error shapes; only retry an explicit transient class and never fan out invalid input.

## Review Guidance

Verify providers are selected by server policy, not model-supplied names, and no credential value is read by tests.

## Activity Log

- 2026-08-05T04:00:00Z -- system -- Prompt created.
