---
work_package_id: WP07
title: Явная доставка outbound-артефакта
dependencies:
- WP01
- WP02
requirement_refs:
- FR-001
- FR-003
- FR-004
- FR-007
- FR-011
- NFR-001
- NFR-009
- C-007
tracker_refs:
- hermes-outbound-artifact-delivery-gry
planning_base_branch: codex/fix-telegram-primary-media-scope-20260809
merge_target_branch: codex/fix-outbound-artifact-delivery-20260809
branch_strategy: Пакет реализуется в отдельной task-ветке от exact candidate 907dbea2960907d21e38a9b5f55ac7a10a62864c; push и merge требуют отдельного разрешения.
subtasks:
- T023
- T024
- T025
- T026
phase: Phase 7 - Bounded outbound artifact delivery
assignee: ''
agent: codex
history:
- at: '2026-08-09T00:00:00Z'
  actor: codex
  action: Пакет создан по явно одобренному поведению; runtime status не материализован из-за COORDINATION_BRANCH_DELETED в anchor checkout.
- at: '2026-08-09T10:46:00Z'
  actor: codex
  action: Реализация и обязательный shared-room Telegram group/topic oracle завершены; tool остаётся pure marker, production fix после нового oracle не потребовался.
agent_profile: python-pedro
authoritative_surface: tools/
create_intent:
- tools/artifact_delivery_tool.py
- tests/tools/test_artifact_delivery_tool.py
execution_mode: approved_implementation
model: ''
owned_files:
- tools/artifact_delivery_tool.py
- toolsets.py
- tests/tools/test_artifact_delivery_tool.py
- tests/gateway/test_access_tool_surface.py
- kitty-specs/live-compatible-media-access-01KZ80JR/meta.json
- kitty-specs/live-compatible-media-access-01KZ80JR/spec.md
- kitty-specs/live-compatible-media-access-01KZ80JR/plan.md
- kitty-specs/live-compatible-media-access-01KZ80JR/tasks.md
- kitty-specs/live-compatible-media-access-01KZ80JR/tasks/README.md
- kitty-specs/live-compatible-media-access-01KZ80JR/tasks/WP07-outbound-artifact-delivery.md
role: Python implementer
tags:
- privacy
- telegram
- artifacts
task_type: implement
---

# Рабочий пакет WP07 — явная доставка outbound-артефакта

## Цель и критерии успеха

- Доставлять существующий XLSX/DOCX/PDF/CSV/ZIP или родственный non-image артефакт только в текущий `ResolvedAccessContext.delivery_target`.
- Принимать от модели только явный путь; пользователь, профиль, чат, topic и account не являются model-controlled аргументами.
- Разрешать только regular file внутри bound profile home или текущего workspace после `resolve(strict=True)`; отклонять missing/outside/symlink escape.
- Вернуть structured success с trusted `MEDIA:<absolute-path>`; существующие auto-append и `_deliver_media_from_response` вызывают текущий adapter `send_document` ровно один раз с current event/thread metadata. Tool сам не отправляет.
- Не менять MEDIA/TTS/image/voice behavior и не сканировать произвольный terminal stdout.

## Ограничения

- Для family/shared использовать существующую `documents` capability; owner сохраняет полный file toolset, shared-room membership не повышает роль.
- Не добавлять dependency, generic path scraping, owner/home fallback, retry или новый target surface.
- Не выполнять push, merge, deploy, restart, config/symlink mutations или Telegram messages.

## Подзадачи

### T023 — Boundary RED

- Проследить registration, schema filtering и dispatch до adapter.
- Через real tool registration/dispatch, bound family/private context и captured Telegram adapter воспроизвести отсутствие доставки synthetic XLSX.
- Сохранить точный RED output до production patch.
- Завершено: сохранён RED `13 failed, 4 passed`; после реализации focused matrix был зелёным.

### T024 — Negative RED matrix

- Проверить отказ для неизвестного/model-supplied target, missing/malformed/mismatched context, outside-profile path, symlink escape и missing `documents`.
- Для каждого отказа проверить отсутствие trusted tag и adapter call.
- Завершено: добавлен настоящий allowed shared-room group/topic oracle и forged foreign-thread denial без tag/send.

### T025 — Минимальная реализация

- Добавить один структурированный tool в существующий `file` toolset.
- Повторно валидировать bound context против current MessageEvent target, вычислить разрешённые roots только из typed profile/workspace и вернуть trusted `MEDIA:` tag без прямого adapter call.
- Возвращать компактный structured success/failure без destination или raw provider error; доставку оставить существующим gateway auto-append/media path.
- Завершено: `deliver_artifact` не вызывает adapter и только возвращает trusted marker после fail-closed проверок.

### T026 — Проверки и review

- Прогнать focused boundary tests, затронутые send_message/document/access/shared/profile media suites и primary photo/voice regressions.
- Прогнать Ruff, `py_compile`, `git diff --check`.
- Проверить exact diff на fail-closed semantics, отсутствие новой роли и неизменность media behavior; commit только task-owned файлов.
- Завершено: новый focused run `52 passed`; отдельные shared/topic/document/primary-media/voice-v0 команды `1 + 46 + 44 + 2 + 3 passed`; Ruff, `py_compile` и diff-check зелёные.

## Runtime limitation

Spec Kitty 3.2.5 продолжает разрешать mission из базового anchor checkout, где `meta.json` ссылается на удалённую coordination branch. В task-ветке metadata минимально flattened, но WP07 нельзя безопасно перевести в runtime lane до landing этой metadata; status events вручную не подделываются.
