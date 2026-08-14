---
work_package_id: WP01
title: Full-boundary regression и минимальный ingress fix
dependencies: []
requirement_refs:
- FR-001
- FR-002
- FR-003
- FR-004
- NFR-001
- NFR-002
- NFR-003
tracker_refs:
- hermes-cf7
planning_base_branch: codex/owner-default-profile-integration
merge_target_branch: codex/owner-default-profile-integration
branch_strategy: Planning artifacts for this mission were generated on codex/owner-default-profile-integration. During /spec-kitty.implement this WP may branch from a dependency-specific base, but completed changes must merge back into codex/owner-default-profile-integration unless the human explicitly redirects the landing branch.
subtasks:
- T001
- T002
- T003
- T004
phase: Bounded bugfix
agent: "codex"
assignee: "codex"
history:
- at: '2026-08-14T06:04:03Z'
  actor: codex
  action: Пакет одобрен; создан Bead hermes-cf7.
- at: '2026-08-14T06:14:00Z'
  actor: codex
  action: RED воспроизвёл реальный ingress denial; минимальный patch и затронутые suites зелёные.
authoritative_surface: tests/gateway/
create_intent: []
execution_mode: code_change
owned_files:
- gateway/run.py
- tests/gateway/test_shared_room_model_picker.py
- tests/gateway/test_slash_access_dispatch.py
tags: []
task_type: implement
---

# WP01 — `/model` в корневой shared-room

## Цель

Провести `/model` через реальный ingress для авторизованной корневой комнаты,
сохранив fail-closed isolation и локальность model selection.

## Выполнение

1. T001 — сначала добавить full-dispatch test и получить RED из
   `_check_slash_access`.
2. T002 — закрепить deny-oracles для global, unknown/unauthorized, other command
   и mismatched callback.
3. T003 — изменить только условие допуска lane-local `/model`.
4. T004 — GREEN, affected suites, статические проверки, review и commit.

## Границы

Нельзя менять roles, profiles, sessions, memory, toolsets, callback target
schema, config/env или live service. Нельзя ослаблять identity/profile binding.

## Доказательство

Сохранить exact RED/GREEN commands and counts, итоговый commit и подтверждение
отсутствия live mutation.

## Результат

- RED: 2 падения для `/model` и `/model@bot` с точным shared-chat denial.
- GREEN: focused `14 passed`; access/profile/session пакет `376 passed`;
  дополнительные model-picker пакеты `41 passed` и `45 passed`.
- Production diff: одно условие в `_check_slash_access`; роли, профили, memory,
  sessions, toolsets и live runtime не менялись.

## Activity Log

- 2026-08-14T10:09:13Z – codex – Implementation already completed at bab2b18d; restoring canonical lifecycle before PR merge.
