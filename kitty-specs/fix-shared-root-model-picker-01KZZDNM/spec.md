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

## Одобренный material delta: `/verbose` и базовый прогресс (2026-08-20)

Пользователь явно поручил заменить неудобное циклическое переключение
`/verbose` на прямой выбор и включить подробный прогресс как базовую настройку
во всех профилях Gurra.

### Дополнительные приёмочные сценарии

1. **Given** `/verbose` доступен в текущей lane, **when** команда вызвана без
   аргументов на Telegram, **then** открывается picker с режимами `OFF`, `NEW`,
   `ALL`, `VERBOSE`, `LOG`, текущий режим отмечен и команда сама режим не меняет.
2. **Given** допустимый аргумент, **when** вызвано `/verbose verbose|all|new|off|log`,
   **then** выбранный режим сохраняется сразу в профиль, обслуживающий lane.
3. **Given** `/verbose next`, **when** команда вызвана, **then** сохраняется
   совместимое циклическое переключение.
4. **Given** registry-owned shared room, **when** открывается picker или
   сохраняется выбор, **then** используется проверенный общий Telegram adapter
   без owner/default fallback для неизвестной identity.
5. **Given** любой активный профиль Gurra, **when** начинается следующий
   Telegram turn без локального выбора пользователя, **then** эффективный
   `tool_progress` равен `verbose`.

### Ограничения delta

- Не меняются роли, toolsets, capabilities, модели, fallback chain и
  административные права.
- Секреты, auth-файлы и platform tokens не читаются и не изменяются.
- Развёртывание выполняется только на HOSTKEY staging с isolated candidate и
  проверяемым rollback на предыдущий candidate.

## Одобренный material delta: topic-wide model routing и cost safety (2026-08-22)

Пользователь подтвердил `делай` после runtime-диагностики: shared-room profiles
без `model/provider` неявно выбирают платный `openrouter/z-ai/glm-5.2`, а
сохранённый `/model` включает sender identity и поэтому не применяется к
другому участнику того же Telegram topic.

### Дополнительные приёмочные сценарии

1. `room-drafts` и `room-research` без lane override используют
   `openai-codex/gpt-5.6-luna`, а не неявный OpenRouter GLM.
2. `/model`, выбранная авторизованным участником shared-room, применяется к
   другому авторизованному участнику той же комнаты/topic, но не другой lane.
3. Старый sender-bound override безопасно мигрируется без секретов.
4. Fallback идёт через direct DeepSeek и free Kimi; paid OpenRouter не является
   неявным fallback, но остаётся доступен при явном выборе.
5. Payment/credit error OpenRouter не вызывает повторный первичный GLM-запрос
   на следующем shared turn без явного OpenRouter override.

### Ограничения delta

- Административные права, capabilities, toolsets и memory isolation не меняются.
- Auth/token files не читаются и не изменяются.
- Live rollout ограничен HOSTKEY staging и проверяемым runtime canary.
