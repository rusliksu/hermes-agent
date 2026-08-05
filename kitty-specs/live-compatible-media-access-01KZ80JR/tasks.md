---
description: "Work package task list for live-derived Gurra access and media isolation"
---

# Work Packages: Live-compatible Gurra access and media isolation

**Inputs**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`
**Prerequisites**: current live-derived branch `a4096896ed92d1edb3dd02e62876dc0fc1ce140a`; no implementation from the divergent experimental branch may be copied wholesale.
**Tests**: every behavior package includes contract/negative tests; all canaries use synthetic/redacted fixtures and never read credential contents.

---

## Work Package WP01: Six-field access contract and fail-closed resolver (Priority: P0)

**Goal**: Add the immutable six-field `ResolvedAccessContext`, typed role/principal/room bindings, capability intersection and exact identity resolver without changing live behavior until the feature gate is enabled.
**Independent Test**: Unit/contract matrix resolves Руслан(owner), Юля(family_sandbox), мама/other family(family_standard), two rooms(shared_room), unknown and malformed identities to the expected context or a deny result before model/session/tools.
**Prompt**: `/tasks/WP01-access-contract.md`
**Requirement Refs**: FR-001, FR-002, FR-004, FR-007

### Included Subtasks

- [x] T001 Add `gateway/access_registry.py` with exactly six serialized context fields, immutable bindings and redacted deny reasons.
- [x] T002 Extend `gateway/profile_routing.py` for exact DM identity and explicit room/topic matching; reject ambiguous routes and missing profiles.
- [x] T003 Bridge `gateway/authz_mixin.py` and ingress call sites so unknown/malformed sources are denied before model/session/tools with no owner/default fallback.
- [x] T004 [P] Add `tests/gateway/test_access_registry.py` and `tests/gateway/test_profile_routing_fail_closed.py` with the full principal/room/unknown matrix.

### Dependencies

- None (starting package).

### Risks & Mitigations

- Legacy adapters may omit trusted account/user metadata; reject rather than infer from username/display name.
- Existing single-principal/group policy must remain readable; put the new resolver behind an explicit compatibility gate and test both modes.

## Work Package WP02: Profile, session and background isolation (Priority: P0)

**Goal**: Carry one resolved context through profile home, session key, memory/session search, attachments, callbacks, cron, compaction/reset/restart and delegation.
**Independent Test**: Pairwise canaries with guessed session IDs, filenames and memory keys show zero observations across two family profiles, owner, sandbox and rooms while concurrent turns remain isolated.
**Prompt**: `/tasks/WP02-runtime-isolation.md`
**Requirement Refs**: FR-003, FR-005, FR-007

### Included Subtasks

- [x] T005 Bind the context in `gateway/session_context.py` and `gateway/run.py` at ingress and clear it at turn completion/cancellation.
- [x] T006 Remove default/active `HERMES_HOME` fallback for a rejected or missing profile; make foreign `profile_id`/session namespace arguments fail closed.
- [ ] T007 [P] Update `tools/session_search_tool.py`, `tools/memory_tool.py` and file/attachment guards to derive namespace only from trusted runtime context.
- [ ] T008 [P] Add negative tests for callbacks, background tasks, cron delivery, delegation, compaction/reset/restart and simultaneous profiles.

### Dependencies

- Depends on WP01.

### Risks & Mitigations

- ContextVars can be lost in thread/executor bridges; add explicit propagation tests and ensure subprocess environment has no process-global identity fallback.

## Work Package WP03: Scoped image/STT/TTS provider facade (Priority: P1)

**Goal**: Add ordered media provider policy with capability checks, retry classification, opaque secret references and redacted audit while retaining current provider implementations.
**Independent Test**: Synthetic providers verify image `openai-codex → fal → openrouter`, STT `local → mistral → openai → elevenlabs`, TTS `edge → openai → elevenlabs`, one attempt per provider and permanent-error stop.
**Prompt**: `/tasks/WP03-media-provider-facade.md`
**Requirement Refs**: FR-006, FR-007

### Included Subtasks

- [x] T009 Add `tools/media_provider_routing.py` with typed image/STT/TTS policies, capability intersection and retry/error normalization.
- [x] T010 Integrate `tools/image_generation_tool.py`, `tools/transcription_tools.py` and `tools/tts_tool.py` through the facade without copying secrets into tool environment or prompts.
- [x] T011 [P] Add `tests/test_media_provider_routing.py` and redaction tests for provider outcomes, secret references and unknown tools.
- [x] T012 Verify the current legacy provider paths remain unchanged when the compatibility policy is disabled.

### Dependencies

- Depends on WP01.

### Risks & Mitigations

- Providers return incompatible error/result shapes; normalize to a small internal result and log only provider name, status and class.

## Work Package WP04: Policy validation, dry-run and dashboard audit surface (Priority: P1)

**Goal**: Validate role/principal/room/media configuration, expose redacted effective policy preview and enforce the break-glass lease contract without transmitting inspected data to the model.
**Independent Test**: CLI/dash dry-run rejects malformed policies, shows effective capabilities for Руслан/Юля/маму/rooms, and lease tests require reason, expire at 15 minutes and produce metadata-only audit.
**Prompt**: `/tasks/WP04-policy-dry-run-audit.md`
**Requirement Refs**: FR-004, FR-006, FR-008

### Included Subtasks

- [x] T013 Extend `hermes_cli/config.py` and `hermes_cli/subcommands/config.py` with policy parse/check/dry-run and redacted output.
- [ ] T014 Add dashboard `Access / Users` endpoints/UI integration on existing localhost/SSH-authenticated surface; no new external listener.
- [ ] T015 [P] Add lease/audit tests proving no bulk search/export, no model delivery and manual early revoke.

### Dependencies

- Depends on WP01 and WP03.

### Risks & Mitigations

- Legacy config keys can accidentally broaden access; validate unknown keys and treat malformed policy as deny.

## Work Package WP05: Migration and profile/room fixture tooling (Priority: P1)

**Goal**: Provide dry-run migration report, per-principal DM ownership mapping, isolated profile/room fixture setup and read-only ambiguous legacy archive.
**Independent Test**: Synthetic migration counts/hashes are stable; no global memory or ambiguous record appears in a family profile; rollback leaves source records intact.
**Prompt**: `/tasks/WP05-migration-fixtures.md`
**Requirement Refs**: FR-003, FR-009

### Included Subtasks

- [ ] T016 Add redacted migration planner/report and profile/room fixture setup under `tests/fixtures/` and `docs/ops/`.
- [ ] T017 Preserve session IDs/timestamps only for unambiguous DM ownership; route ambiguous rows to a closed read-only archive.
- [ ] T018 [P] Add migration hash/count and no-global-memory tests; do not read or copy credential/auth files.

### Dependencies

- Depends on WP01 and WP02.

### Risks & Mitigations

- Ownership cannot be inferred safely from names or content; classify as ambiguous and leave it archived.

## Work Package WP06: Staging canary, rollback and live gate packet (Priority: P1)

**Goal**: Produce a live-derived compatibility release, synthetic gateway/dashboard canary, redacted evidence, rollback rehearsal and explicit live-cutover checklist.
**Independent Test**: Staging services remain healthy, dashboard is loopback-only, all privacy/media canaries pass, and rollback restores the captured live code/config surfaces.
**Prompt**: `/tasks/WP06-staging-canary-rollback.md`
**Requirement Refs**: FR-010

### Included Subtasks

- [ ] T019 Build candidate from current live HEAD on the task-owned branch; record changed-file manifest and SHA-256 evidence without credentials.
- [ ] T020 Run owner/Юля/мама/other-family/room/unknown synthetic canaries plus media policy dry-run and service health checks.
- [ ] T021 [P] Document backup, rollback and separate live restart/Telegram canary gate in `docs/ops/media-access-canary-rollback.md`.
- [ ] T022 Stop before live mutation; execute config apply/restart only after a new explicit user approval.

### Dependencies

- Depends on WP01, WP02, WP03, WP04 and WP05.

### Risks & Mitigations

- Live branch has deployment-specific assumptions; compare service state before/after and keep exact prior code/config references for rollback.

## Dependency & Execution Summary

- **Sequence**: WP01 → WP02 and WP03 (parallel after contract) → WP04 and WP05 → WP06.
- **MVP Scope**: WP01 + WP02 + WP03 + focused tests; no live rollout.
- **Parallelization**: WP02 and WP03 touch disjoint primary modules after WP01. WP04 and WP05 can proceed after their listed prerequisites; WP06 is strictly last.
- **Live gate**: T022 is a hard stop until a separate explicit approval; implementation approval does not imply restart/deploy permission.

## Requirements Coverage Summary

| Requirement ID | Covered By Work Package(s) |
|----------------|----------------------------|
| FR-001 | WP01 |
| FR-002 | WP01 |
| FR-003 | WP02, WP05 |
| FR-004 | WP01, WP04 |
| FR-005 | WP02 |
| FR-006 | WP03, WP04 |
| FR-007 | WP01, WP02, WP03 |
| FR-008 | WP04 |
| FR-009 | WP05 |
| FR-010 | WP06 |

## Subtask Index (Reference)

| Subtask ID | Summary | Work Package | Priority | Parallel? |
|------------|---------|--------------|----------|-----------|
| T001 | Six-field context | WP01 | P0 | No |
| T004 | Identity matrix tests | WP01 | P0 | Yes |
| T005 | Context binding | WP02 | P0 | No |
| T009 | Media facade | WP03 | P1 | No |
| T013 | Policy dry-run | WP04 | P1 | No |
| T016 | Migration planner | WP05 | P1 | No |
| T019 | Staging artifact | WP06 | P1 | No |
| T022 | Live gate stop | WP06 | P1 | No |
