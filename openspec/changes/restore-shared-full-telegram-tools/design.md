## Контекст

Telegram gateway сначала вычисляет стандартные platform toolsets, затем для
shared scope заменяет их на memory-only. Отдельно `_bind_shared_memory`
предполагает, что единственным schema tool является `memory`. Это две связанные
причины регрессии. При этом shared session уже имеет отдельный transcript,
MemoryStore и prompt без private context prefill.

## Цели / вне целей

**Цели:**

- убрать special-case memory-only и сохранить штатное вычисление Telegram tools;
- оставить scoped shared MemoryStore и приватную context isolation;
- сохранить owner-only authority для approval/elevated действий;
- доказать shared/private cache separation и fail-closed group policy;
- доставить immutable artifact с безопасным откатом.

**Вне целей:**

- добавлять новый browser/web backend, подписку или credential;
- расширять allowlist комнат/топиков;
- возвращать owner private files, DM transcript или identity prompt;
- менять общую архитектуру tool registry.

## Решения

### 1. Один источник tool profile

Shared turn не переопределяет `enabled_toolsets` и `disabled_toolsets`. Он
наследует уже вычисленный configured Telegram profile и его availability
filtering. Это автоматически сохраняет parity при последующих изменениях
конфигурации и не создаёт второго списка tools.

### 2. Scoped memory является binding, а не единственным tool

`_bind_shared_memory` требует наличие `memory`, но допускает остальные schemas.
Он заменяет только MemoryStore на shared scoped store и не влияет на другие
tools.

### 3. Privacy boundary остаётся независимой

Shared session продолжает пропускать private context files/prefill/history и
использует отдельные session/memory identifiers. System prompt прямо сообщает,
что полный configured tool profile доступен, но owner private context не
доступен.

### 4. Approval boundary не расширяется вместе с tools

Shared участник может инициировать разрешённый tool call, но approve/deny,
admin, pairing и elevated authority по-прежнему требуют owner principal.
Владелец распознаётся по Telegram user ID и внутри exact allowlisted shared
scope; неизвестная группа не расширяет его elevated surface.

### 5. Cache separation является delivery gate

Agent signature должна включать эффективные toolsets и shared/private prompt
inputs. Tests подтверждают, что shared turn не переиспользует private agent.

## Риски / компромиссы

- [Host impact от terminal/files/code] → exact room allowlist, существующие
  sandbox/approval guards, owner-only elevation и negative tests.
- [Private context leak] → отдельные shared session/memory keys, no context
  prefill и cache-isolation tests.
- [Конфигурационный drift] → единый Telegram profile resolver вместо второго
  hardcoded списка.
- [Неуспешный rollout] → immutable candidate, атомарный symlink switch,
  сохранённый previous target и user-service rollback.

## План миграции

1. Реализовать минимальный patch и focused/security tests в clean worktree.
2. Запустить Ruff, project test runner и strict OpenSpec validation.
3. Собрать immutable staging candidate и выполнить safe canaries.
4. Зафиксировать exact old/new targets, атомарно переключить symlink и
   перезапустить только user gateway.
5. Проверить health/logs/Telegram; при ошибке вернуть previous target.
