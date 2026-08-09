# Каталог рабочих пакетов

Здесь находятся prompt-файлы рабочих пакетов (WP) текущей mission.

Активный workflow: Spec Kitty + Beads. Spec Kitty владеет статусом mission и
event log, а Beads --- issue identity, приоритетом и зависимостями.

Исторический Beads issue mission: `HERMES-4t0`.
Текущий bounded WP08: `tm-ai-loopx-kimi-86x` с
`spec_id: live-compatible-media-access-01KZ80JR`. Runtime lane не
материализуется вручную из-за существующего `COORDINATION_BRANCH_DELETED`.

## Структура каталога (v0.9.0+)

```
tasks/
├── WP01-setup-infrastructure.md
├── WP02-user-authentication.md
├── WP03-api-endpoints.md
└── README.md
```

Все WP-файлы лежат непосредственно в `tasks/`. Статус хранится в
`status.events.jsonl`, а не во frontmatter WP.

## Формат файла рабочего пакета

Каждый WP-файл **обязан** использовать YAML frontmatter:

```yaml
---
work_package_id: "WP01"
title: "Work Package Title"
dependencies: []
planning_base_branch: "codex/live-compatible-media-cutover"
merge_target_branch: "codex/live-compatible-media-cutover"
branch_strategy: "Planning artifacts were generated on codex/live-compatible-media-cutover; completed changes must merge back into codex/live-compatible-media-cutover."
subtasks:
  - "T001"
  - "T002"
phase: "Phase 1 - Setup"
assignee: ""
agent: ""
shell_pid: ""
history:
  - timestamp: "2025-01-01T00:00:00Z"
    agent: "system"
    action: "Prompt generated via /spec-kitty.tasks"
---

# Work Package Prompt: WP01 -- Work Package Title

[Content follows...]
```

## Отслеживание статуса

Статус отслеживается через канонический event log (`status.events.jsonl`), а не
через frontmatter WP. Для изменения lane используйте `spec-kitty agent tasks
move-task`:

```bash
spec-kitty agent tasks move-task <WPID> --to <lane>
```

Пример:
```bash
spec-kitty agent tasks move-task WP01 --to doing
```

## Имена файлов

- Формат: `WP01-kebab-case-slug.md`
- Примеры: `WP01-setup-infrastructure.md`, `WP02-user-auth.md`
