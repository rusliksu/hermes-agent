---
work_package_id: WP08
title: Bound создание и подтверждённая доставка generated document
dependencies:
- WP01
- WP02
- WP07
requirement_refs:
- FR-003
- FR-005
- FR-007
- FR-011
- FR-012
- NFR-001
- NFR-009
- NFR-010
- C-008
tracker_refs:
- tm-ai-loopx-kimi-86x
planning_base_branch: codex/fix-artifact-bound-workspace-delivery-20260809
merge_target_branch: codex/fix-artifact-bound-workspace-delivery-20260809
branch_strategy: Пакет реализуется в текущем bounded worktree от exact HEAD d4ce85428460ebacf808c6de5d26ca2599079c05; push, merge и live mutations запрещены.
subtasks:
- T027
- T028
- T029
- T030
phase: Phase 8 - Bound workspace artifact correction
assignee: codex
agent: codex
history:
- at: '2026-08-09T12:18:55Z'
  actor: codex
  action: Материальный delta явно одобрен пользователем через @best-step; Bead переведён в in_progress. Runtime lane не материализован из-за COORDINATION_BRANCH_DELETED.
agent_profile: python-pedro
authoritative_surface: agent/ и gateway/
execution_mode: approved_implementation
owned_files:
- agent/artifact_delivery_stop.py
- agent/conversation_loop.py
- agent/turn_context.py
- agent/turn_finalizer.py
- gateway/run.py
- gateway/platforms/base.py
- run_agent.py
- tools/artifact_delivery_tool.py
- tools/file_tools.py
- tests/gateway/test_artifact_bound_workspace_delivery.py
- kitty-specs/live-compatible-media-access-01KZ80JR/spec.md
- kitty-specs/live-compatible-media-access-01KZ80JR/plan.md
- kitty-specs/live-compatible-media-access-01KZ80JR/tasks.md
- kitty-specs/live-compatible-media-access-01KZ80JR/tasks/README.md
- kitty-specs/live-compatible-media-access-01KZ80JR/tasks/WP08-artifact-bound-workspace-delivery.md
role: Python implementer
tags:
- privacy
- telegram
- artifacts
task_type: implement
---

# Рабочий пакет WP08 — bound создание и подтверждённая доставка generated document

## Цель и критерии успеха

- Generated documents для bound `family`/`shared_room` остаются внутри текущих trusted profile/workspace roots.
- Unsafe/out-of-root generated artifact с legacy `MEDIA:`/success text получает не более одной corrective continuation и никогда не образует loop.
- Correction создаёт новый safe artifact внутри bound workspace и вызывает существующий `deliver_artifact`; arbitrary outside file автоматически не копируется.
- Success claim не отправляется до успешного `send_document` в current immutable `delivery_target` с текущим topic metadata.
- Owner capabilities и unrelated primary photo, voice/STT/TTS, inbound document и plain-text behavior сохраняются.

## Ограничения

- Не ослаблять существующий fail-closed path validator и не добавлять новый service/parser/dependency/target API.
- Переиспользовать `ResolvedAccessContext`, typed bound-root validation, MEDIA extraction и agent continuation machinery.
- Использовать только synthetic IDs/roots; не читать credentials, raw message/session/user/chat IDs или private contents.
- Не выполнять push, merge, deploy, restart, config/env/symlink mutations или Telegram messages.
- Не изменять `status.json`, `status.events.jsonl` или `lanes.json`: Spec Kitty runtime блокируется существующим `COORDINATION_BRANCH_DELETED`.

## Подзадачи

### T027 — Full-boundary RED

- Через real shared-room/topic ingress, AIAgent model/tool sequence, `write_file`, `deliver_artifact`, gateway filter/delivery и captured Telegram adapter воспроизвести unsafe XLS outside roots + legacy `MEDIA:` + success text.
- Проверить отсутствие false-success delivery до production patch и сохранить exact command/output.

#### RED evidence до production code

```text
Command:
HERMES_PYTHON=/home/openclaw/staging/hermes-deploy-live-compatible-25d5031b-20260806T202000Z/.venv/bin/python scripts/run_tests.sh tests/gateway/test_artifact_bound_workspace_delivery.py -q

Output:
[100.0% |     4/~4 | ✓1 | ✗3] ✗ tests/gateway/test_artifact_bound_workspace_delivery.py (1✓ 3✗, 22.9s)
FAILED tests/gateway/test_artifact_bound_workspace_delivery.py::test_outside_generated_xls_gets_one_safe_correction_and_confirmed_topic_delivery
FAILED tests/gateway/test_artifact_bound_workspace_delivery.py::test_second_unsafe_generation_fails_without_loop_or_success_claim
FAILED tests/gateway/test_artifact_bound_workspace_delivery.py::test_success_claim_is_suppressed_when_current_topic_document_send_fails
3 failed, 1 passed in 9.28s
=== Summary: 1 files, 1 tests passed, 3 failed (100% complete) in 22.9s (12 workers) ===
```

Observed production evidence: каждый failing turn выполнил `write_file` и завершился после `api_calls=2/90`; `BasePlatformAdapter.filter_media_delivery_paths` записал `Skipping unsafe MEDIA directive path`, после чего `BasePlatformAdapter` всё равно вызвал text send для `UNSAFE_SUCCESS`. Correction и `send_document` не выполнялись.

### T028 — Negative no-loop/privacy matrix

- Проверить second-failure/no-loop, foreign target, symlink escape и current-context mismatch.
- В каждом negative случае потребовать ноль `send_document`, ноль success claim и отсутствие sensitive path/context leakage.

### T029 — Минимальный root-cause fix

- Проследить всех callers изменяемых функций.
- По ponytail/full выбрать самый узкий общий chokepoint, сохранить role alternation и ограничить correction одной попыткой.
- Удерживать success claim до подтверждённой document delivery; не менять sibling media/text flows.

### T030 — Проверки, review и handoff

- Прогнать focused test и affected artifact delivery, shared/family access, Telegram group/topic/document, primary photo, voice/STT/TTS, inbound attachment и plain-text suites.
- Прогнать Ruff для changed Python, `py_compile`, `git diff --check`; проверить caller provenance и отсутствие privacy/log leakage.
- Получить независимый review, исправить findings, добавить verification note в Bead, оставить его `in_progress` и закоммитить только task-owned diff.

## Runtime limitation

Spec Kitty 3.2.5 разрешает mission через anchor checkout, где declared coordination branch удалена, и возвращает `COORDINATION_BRANCH_DELETED`. WP08 не переводится в runtime lane вручную; `status.events.jsonl` остаётся неизменённым до отдельного восстановления coordination topology.

## Independent review remediation evidence

### Review RED before production edits

```text
Command:
HERMES_PYTHON=/home/openclaw/staging/hermes-deploy-live-compatible-25d5031b-20260806T202000Z/.venv/bin/python scripts/run_tests.sh tests/gateway/test_artifact_bound_workspace_delivery.py -q

Output:
5 failed, 2 passed

Failures proved:
- confirmation mode could derive and send the same absolute document path again on both adapter success and adapter failure;
- the stop guard reduced delivery provenance to a boolean instead of the matching deliver_artifact tool-call ID and exact structured path/tag;
- V4A Add File could mutate an absolute document target outside the bound roots;
- bound document streaming could expose a premature success claim before tool classification.
```

### Expanded focused GREEN

```text
Command:
HERMES_PYTHON=/home/openclaw/staging/hermes-deploy-live-compatible-25d5031b-20260806T202000Z/.venv/bin/python scripts/run_tests.sh tests/gateway/test_artifact_bound_workspace_delivery.py -q

Output:
7 passed, 0 failed
```

The focused suite now covers exact-once confirmed document delivery on adapter success and failure, exact structured tool-result provenance despite earlier image/TTS results, patch containment for outside absolute and safe relative document targets, and buffered streaming with unchanged plain-text replay.

### Exact affected matrix GREEN

```text
Command:
HERMES_PYTHON=/home/openclaw/staging/hermes-deploy-live-compatible-25d5031b-20260806T202000Z/.venv/bin/python scripts/run_tests.sh \
  tests/agent/test_verification_stop_caching.py \
  tests/gateway/test_access_registry.py tests/gateway/test_access_registry_config.py tests/gateway/test_access_registry_ingress.py \
  tests/gateway/test_artifact_bound_workspace_delivery.py tests/gateway/test_platform_base.py tests/gateway/test_run_tool_media_re.py tests/gateway/test_session_context_inheritance.py \
  tests/gateway/test_shared_group_sender_prefix.py tests/gateway/test_shared_topic_full_boundary.py \
  tests/gateway/test_telegram_documents.py tests/gateway/test_telegram_group_gating.py tests/gateway/test_telegram_photo_interrupts.py tests/gateway/test_telegram_primary_media_scope.py tests/gateway/test_telegram_topic_mode.py \
  tests/gateway/test_auto_voice_reply_format.py tests/gateway/test_send_voice_reply_notify.py tests/gateway/test_stt_config.py tests/gateway/test_stt_transcript_echo_config.py tests/gateway/test_telegram_audio_vs_voice.py tests/gateway/test_telegram_voice_duration.py tests/gateway/test_telegram_voice_v0_regressions.py tests/gateway/test_tts_media_routing.py tests/gateway/test_voice_command.py tests/gateway/test_voice_mode_platform_isolation.py \
  tests/gateway/test_discord_attachment_download.py tests/gateway/test_discord_document_handling.py tests/gateway/test_document_cache.py tests/gateway/test_document_context_note.py tests/gateway/test_media_extraction.py tests/gateway/test_mixed_attachment_routing.py \
  tests/tools/test_artifact_delivery_tool.py tests/tools/test_file_tools.py -q

Output:
33 files, 912 tests passed, 0 failed
```

This is the same explicit affected-file matrix as the earlier 865-pass run; the current parametrized collection reports 912 passing cases.

### Static and boundary audit GREEN

- Ruff on all changed Python: `All checks passed!`
- `py_compile` on all changed Python: exit 0.
- `git diff --check`: exit 0.
- Confirmed delivery is selected only from the matching `deliver_artifact` structured tool result and carries its exact call ID, absolute path, and `MEDIA:` tag; the confirmation branch never calls the generic image/TTS/artifact producer scan.
- The adapter suppresses model-derived MEDIA candidates, images, and local files in confirmation mode and invokes `send_document` once for the propagated trusted path.
- Every `patch` mutation target, including absolute V4A Add File targets, passes the shared bound-output validator before file operations. Ordinary source patches and owner behavior remain unchanged.
- Streaming hold state and delivery confirmation reset per turn and remain local to the current run; unrelated bound plain-text output is replayed unchanged. No binder or gateway target reconstruction remains.
- New policy logging contains no raw artifact path, user/chat identity, or owner fallback.

## Cycle-2 review remediation — partial, remains in progress

### P1 strict TDD evidence

RED command:

```text
HERMES_PYTHON=/home/openclaw/staging/hermes-deploy-live-compatible-25d5031b-20260806T202000Z/.venv/bin/python scripts/run_tests.sh tests/gateway/test_artifact_bound_workspace_delivery.py -q
```

RED result: `7 passed, 2 failed`. The no-space `***Add File:` outside-document
case stopped after 2 model calls instead of issuing the one correction; the
outside mutation was rejected, but `UNSAFE_SUCCESS` still reached text delivery.
The second temporary RED showed ordinary bound plain text remained buffered
before turn completion.

P1 now derives all V4A mutation targets from `tools.patch_parser` rather than
maintaining separate safety/stop regex grammars. Focused boundary GREEN:
`8 passed, 0 failed`. Parser/helper/file-tool focused GREEN: `4 files, 155
passed, 0 failed`. The no-space outside target does not mutate, triggers one
correction, and exposes no premature success; a safe relative no-space target
remains accepted.

Affected matrix GREEN: `33 files, 913 passed, 0 failed` (the prior 912 cases
plus the new no-space boundary case). Ruff on changed Python, `py_compile` on
changed Python, and `git diff --check` all exit 0.

### P2 material protocol trade-off — not implemented

The temporary timing RED used an identical `SUCCESS_PREFIX` for an ordinary
no-tool response and for an adversarial response whose document mutation was
announced later. Before that later event, both runs expose the same gateway
input: `stream_delta_callback(text)`. The first mutation-classifying signal is
`tool_start_callback(name, args)`, fired only when the parsed tool call begins
execution. Therefore immediate ordinary-prefix delivery also immediately
delivers the adversarial prefix; waiting for the tool-start signal preserves
the no-premature-success guarantee but delays ordinary plain text.

Existing transports do not close the gap: Telegram family/shared rooms cannot
use native draft streaming (private chats only), progressive edits are already
user-visible, and native drafts are also visible previews. Retraction after
tool start cannot establish “never exposed”. Per the cycle-2 stopping condition,
no heuristic/user-text classifier or P2 production change was added, and the
temporary impossible RED was removed after preserving this evidence. WP08 and
Bead `tm-ai-loopx-kimi-86x` remain `in_progress`; this branch is not ready for
re-review or deployment.

## Cycle-3 provenance remediation — remains in progress

### Strict TDD RED before production edits

Command:

```text
HERMES_PYTHON=/home/openclaw/staging/hermes-deploy-live-compatible-25d5031b-20260806T202000Z/.venv/bin/python scripts/run_tests.sh tests/gateway/test_artifact_bound_workspace_delivery.py -q
```

Result: `8 passed, 10 failed`, exit 1. Production code was unchanged from
`17bb00bee5765df3bd0969f7451c8af927e617d7`. The failures proved that the old
independent boolean accepted a successful delivery result despite a failed
document mutation, a different successful mutation output, a later mutation
that made an earlier delivery stale, missing/malformed/conflicting mutation
results, an outside-root path, and unrelated delivery paths for parser-valid
V4A add/update/move cases.

### Minimal production change

`agent/artifact_delivery_stop.py` now scans ordered server-created tool-result
messages. Successful `write_file`/`patch` results contribute only absolute,
bound, canonical document paths from `files_modified`; model arguments and
assistant claims contribute no provenance. A `deliver_artifact` result can
confirm only when its exact server-resolved `MEDIA:` path follows the latest
successful mutation of that same path. A later mutation makes an earlier
delivery stale. Missing, malformed, failed, conflicting, outside-root, or
multiply qualifying provenance fails closed through the existing single
correction/failure path. No generic file scan or producer scan was added.

### GREEN evidence

- Focused boundary: `18 passed, 0 failed`.
- Parser/helper/file-tool focus (`test_patch_parser`,
  `test_tool_dispatch_helpers`, `test_tool_result_classification`,
  `test_file_mutation_verifier`, `test_file_tools`): `5 files, 159 passed,
  0 failed`.
- Exact prior affected matrix: `33 files, 923 passed, 0 failed` (the prior 913
  plus ten new cases).
- Ruff on both changed Python files: `All checks passed!`.
- `py_compile` on both changed Python files: exit 0.
- `git diff --check`: exit 0.

Containment coverage now includes parser-valid add, update, and move targets;
exact delivery coverage includes the canonical successful path, mismatch,
staleness, malformed/ambiguous results, and outside-root result paths. Existing
full-boundary assertions still prove one confirmed `send_document` and withheld
success text on adapter failure. Streaming/global buffering, prompts, image and
voice paths, inbound media, ordinary final text, roles, configuration, and live
runtime were not changed.

WP08 and Bead `tm-ai-loopx-kimi-86x` remain `in_progress`. This evidence does
not claim independent review or deployment readiness.
