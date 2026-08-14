# Спецификация: `/model` в корневой shared-room

**Ветка:** `codex/fix-shared-root-model-picker-20260814`
**Статус:** одобрено к реализации
**Основание:** реальный Telegram canary 2026-08-14 вернул запрет до открытия picker.

## Сценарий пользователя

Авторизованный участник зарегистрированной общей комнаты вызывает `/model` в
корневой Telegram-группе без forum topic и получает picker моделей для этой
общей lane. Выбор не влияет на owner, личные профили или другие комнаты.

### Приёмочные сценарии

1. **Given** корневая shared-room зарегистрирована в `AccessRegistry`, а
   отправитель является её участником, **when** ingress получает `/model` или
   `/model@bot`, **then** команда проходит `_check_slash_access` и открывает
   picker для текущей shared lane.
2. **Given** та же комната, **when** отправитель вызывает `/model --global`,
   **then** команда отклоняется до изменения настроек.
3. **Given** неизвестная комната, отсутствующий shared binding или посторонний
   отправитель, **when** вызывается `/model`, **then** команда отклоняется
   fail-closed без owner/default fallback.
4. **Given** корневая shared-room, **when** вызываются `/settings`, `/reasoning`,
   `/fast` или административные команды, **then** прежний запрет сохраняется;
   расширяется только локальный `/model`.
5. **Given** callback picker, **when** он обрабатывается, **then** он связан с
   точной текущей shared lane и не может изменить другую комнату или профиль.

## Требования

- **FR-001:** Решение о доступе проверяется на полном dispatch boundary через
  реальный `GatewayRunner._handle_message`, а не прямой вызов handler.
- **FR-002:** Для `/model` в shared chat допустим только lane-local scope;
  `--global` запрещён.
- **FR-003:** Авторизация использует server-owned registry binding и текущую
  transport identity; имя пользователя и аргументы модели права не дают.
- **FR-004:** Topic behavior остаётся прежним; root-group получает только
  model picker, остальные topic-only команды не расширяются.
- **NFR-001:** Нет изменений в memory/session/workspace/profile isolation.
- **NFR-002:** Неавторизованные и malformed contexts отклоняются до handler.
- **NFR-003:** Regression воспроизводит реальный ingress gate и проверяет
  отсутствие обхода через callback metadata.

## Вне scope

- Изменение ролей, профилей, toolsets, memory/session routing.
- Разрешение глобальных model settings в shared chats.
- Deploy, restart, config/env/symlink mutation и Telegram canary.
- Рефакторинг slash-command policy вне минимального условия `/model`.

## Gate

Сначала RED на полном dispatch boundary, затем минимальный production patch,
focused и затронутые privacy/access suites, Ruff, `py_compile` и
`git diff --check`. Live rollout остаётся отдельным явным gate.
