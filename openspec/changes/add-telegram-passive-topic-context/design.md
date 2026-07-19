## Context

Hermes уже умеет записывать unmentioned Telegram messages как transcript rows с
`observed=true`, не вызывая message handler, и перед следующим адресованным turn
выносить их в отдельный context-only block. Single-principal rollout намеренно
запретил этот path, потому что legacy observation полагался на
`group_allowed_chats`, удалял sender из triggered source и не имел prompt window.

Удаление sender безопасно для legacy session key, но ломает authoritative
single-principal auth: `SharedTelegramScope` требует идентифицируемого человека.
При этом `SinglePrincipalPolicy.group_sessions_per_user()` уже создаёт общий
session key для адресованного turn, поэтому новый storage/session слой не нужен.

## Goals / Non-Goals

**Goals:**

- повторно использовать существующий observed-transcript path;
- авторизовать observation exact shared policy, а не legacy grants;
- сохранить sender на triggered event для auth и убрать его только со source
  passive transcript;
- ограничить prompt replay по age/count/chars;
- доказать root/General/topic/group isolation и отсутствие agent side effects.

**Non-Goals:**

- свободный ответ Gurra без mention/reply/addressed command;
- passive media download, transcription или document parsing;
- новый cache service, table, queue, dependency или config surface для лимитов;
- автоматическая запись observed text в scoped long-term memory;
- чтение Telegram history до момента получения Bot API update.

## Decisions

### 1. Переиспользовать transcript rows `observed=true`

Adapter продолжит вызывать существующий `_observe_unmentioned_group_message`, а
runner — `_build_gateway_agent_history` и
`_wrap_current_message_with_observed_context`. Это минимальный ponytail path:
restart persistence, dedup по message ID и current-message separation уже
реализованы. Отдельный in-memory deque отклонён: он дублирует routing и теряет
контекст при restart.

### 2. Single-principal policy является единственным observation allowlist

При enabled policy adapter вызывает `policy.shared_scope()` для исходного
Telegram message. `None` означает deny; legacy `group_allowed_chats`, guest mode
и wake-word не рассматриваются. Explicit config flag observation остаётся
обязательным fail-closed switch.

Для passive write source теряет sender и сохраняет chat/thread. Для следующего
trigger source сохраняет sender до gateway auth; общий session key уже
обеспечивает policy hook. Это устраняет причину прежнего полного запрета без
изменения auth API.

### 3. Bounded replay применяется в runner, а audit transcript не переписывается

Runner отбирает timestamped observed rows за последние 6 часов, затем newest
suffix до 50 сообщений и 20 000 символов. Старые rows по-прежнему исключаются из
обычной conversation history и остаются в SQLite для audit/rollback. DB pruning
или schema migration не нужны.

Лимиты являются constants, а не config knobs: это первый bounded rollout, и
операторская вариативность пока не оправдывает дополнительную policy surface.

### 4. Single-principal attribution не передаёт raw IDs

Observed и current addressed text используют display label участника; если его
нет, применяется нейтральный `participant`. Legacy behavior не меняется.
Observation success log содержит только категорию без chat/user IDs и body.

### 5. Первый rollout принимает только text

Для single-principal observation сообщения без `message.text` отвергаются.
Существующий legacy media observation остаётся неизменным. Это исключает
фоновые downloads/cache side effects и удерживает scope в границах доказанного
пользовательского кейса.

## Risks / Trade-offs

- [BotFather privacy mode не доставляет unmentioned updates] → live canary
  проверяет фактическое получение; код не может восстановить недоставленное.
- [SQLite transcript растёт от group chatter] → prompt bounded; существующий
  session pruning остаётся storage policy, отдельная cleanup migration не нужна.
- [Новый участник группы может спросить недавний context] → это явная семантика
  shared room; private allowlist содержит только доверенные группы.
- [Display names совпадают] → модель может не различить одноимённых участников;
  raw IDs намеренно не раскрываются, расширение pseudonym mapping отложено.
- [Clock skew] → timestamps создаются gateway в UTC; строки без валидного времени
  fail-closed не попадают в prompt.

## Migration Plan

1. Добавить policy-aware observation gate и bounded replay tests.
2. Прогнать targeted Telegram/auth/session suites и broader regressions.
3. Слить Hermes PR и Gurra OpenSpec/ops PR после strict validation и review.
4. Создать backup active refs/config, включить private observation flag,
   выполнить preflight и restart только gateway.
5. Проверить real DM/group/topic/unknown-group canaries без записи их текста.

Rollback возвращает Hermes/Gurra refs и private config, затем перезапускает
только gateway. Observed transcript rows не удаляются и остаются недоступны
старому single-principal path.

## Open Questions

Нет блокирующих вопросов. Расширение окна и passive media возможно только новым
material delta после измерений live usage.

