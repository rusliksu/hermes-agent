## Context

Hermes уже разделяет Telegram conversations каноническим `session_key` и умеет сохранять legacy `/model` override в текущем `SessionEntry`. Однако этот override очищается на `/new`, `/reasoning` живёт только в памяти, а `/fast` меняет global config. Telegram model/choice picker хранят единственный state на `chat_id`, не имеют TTL и полной identity binding; model callback также не проверяет инициатора, а acknowledgement выполняется после потенциально долгого действия.

## Goals / Non-Goals

**Goals:**

- единая `/settings` card с topic-scoped model/reasoning и session-scoped Fast API;
- долговечные preferences, независимые от transcript/session lifecycle;
- единый scope parser и application path для typed command и picker;
- nonce/TTL/identity-bound Telegram callbacks и безопасная конкурентность;
- один topic-aware status bubble и Stop, использующий существующий cancellation path;
- сохранение prompt caching и существующей session authorization.

**Non-Goals:**

- автоматический smart model routing;
- новый provider или credential store;
- inline session picker v1;
- BotFather configuration;
- изменение raw session transcripts или authorization rules.

## Decisions

### Preferences хранятся отдельно от SessionEntry

В `state.db` добавляется `gateway_topic_preferences(scope, lane_key, preferences_json, updated_at)` с primary key `(scope, lane_key)`. `SessionEntry` не подходит: она пересоздаётся на `/new`, auto-reset, compression recovery и `/resume`, а pruning может удалить её вместе с transcript route.

`lane_key` — versioned SHA-256 канонического JSON tuple `profile, platform, chat_type, chat_id, thread_id, user_id_alt|user_id`, построенного после существующей Telegram source normalization. Preference JSON допускает только sanitized `model/provider/base_url` и canonical `reasoning_effort`; credentials, api mode, credential pool и service tier отбрасываются. Provider credentials каждый раз разрешаются существующим runtime resolver.

### Приоритет соответствует утверждённому контракту

Model resolution: topic preference → legacy session override → channel override → global. Reasoning resolution: topic preference → legacy session override → per-model/global config. Это означает, что `--session` не маскирует уже существующую topic preference; пользователь может очистить/заменить topic preference через topic scope. Этот неочевидный результат фиксируется тестом, потому что порядок был явно утверждён.

Fast API хранится в отдельном in-memory presence-sensitive session map и разрешается перед global service tier. Он не попадает в topic store и очищается на `/new`, auto-reset, compression reset и смене/восстановлении transcript. За основу берётся уже существующая upstream session-scoped реализация, адаптированная минимальным diff.

### Один parser scope и один applier на настройку

Общий parser принимает взаимоисключающие scope tokens. Model/reasoning поддерживают session/topic/global; в Telegram default — topic. Fast поддерживает session/global, default — session, а `--topic` отклоняется как несовместимый с transcript-scoped контрактом. Typed commands, `/settings` и вложенные picker вызывают одни и те же model/reasoning/fast applier-функции.

### `/settings` — Telegram hub card

Команда регистрируется в общем registry, но rich card отправляется Telegram adapter. Card показывает эффективные values и имеет независимые кнопки Luna/Terra/Sol/All models, low/medium/high/Advanced, Fast и Close. `Advanced` отображается дружелюбным label и нормализуется существующим reasoning parser к максимальному поддерживаемому effort. После изменения card перерисовывается; All models открывает существующий model flow с topic scope и безопасным возвратом.

### Picker state адресуется nonce

Model, generic choice, settings и run-status states хранятся по `secrets.token_urlsafe(9)` nonce. Callback data передаёт только короткий prefix, nonce, action kind и числовой index, поэтому гарантированно укладывается в Telegram 64 bytes. Server state содержит picker type, фактические message/chat/thread, initiator user, session/lane key, monotonic expiry и bounded payload.

На callback выполняются: strict parse → lookup/prune/TTL → existing allowlist auth → exact user/chat/thread/message/session check → immediate `query.answer()` → действие. Невалидный callback получает быстрый безопасный ответ и не меняет state. Terminal action/Close удаляет только свой nonce; navigation/card refresh сохраняет или атомарно заменяет собственный state. Два меню одного чата не перезаписывают друг друга.

Если Telegram fallback отправил picker вне исходного thread, state связывается с фактическим returned message/thread; при невозможности доказать соответствие picker считается неотправленным.

### Status/Stop переиспользует текущие механизмы

Ключ cache существующего `send_or_update_status` расширяется thread/session identity, чтобы статусы соседних топиков не конфликтовали. Для активного run adapter добавляет короткий `rs:<nonce>:x` Stop callback, привязанный к owner/lane/generation. Callback делегирует существующему `/stop` cancellation path; отдельная модель отмены не создаётся. На completion state и keyboard очищаются.

## Risks / Trade-offs

- [Новая SQLite table требует migration] → idempotent `CREATE TABLE IF NOT EXISTS`, изолированные CRUD tests и отсутствие FK к transcript sessions.
- [Хеш lane key затрудняет диагностику] → безопасные debug logs показывают только короткий hash/profile/platform без raw IDs; lookup всегда детерминирован.
- [Card и model picker образуют вложенный flow] → единый nonce state с явным parent/settings action, bounded TTL и тест возврата.
- [ACK слишком ранний для ошибки применения] → ошибки после ACK показываются редактированием card/сообщением; Telegram spinner при этом не зависает.
- [Stop может попасть в завершившийся generation] → nonce содержит generation binding, completion удаляет state, stale callback не вызывает общий `/stop` для нового run.
- [Новые resolution lookup могут ухудшить hot path] → индексированный primary-key read и небольшой per-runner cache с invalidation при update; prompt-cache key включает эффективную модель, но reasoning остаётся per-message как сейчас.

## Migration Plan

1. Добавить topic preference schema/CRUD, key builder и sanitization tests.
2. Подключить model/reasoning resolution и scope appliers; затем session Fast map и lifecycle cleanup.
3. Перевести существующие picker на nonce state и добавить security/concurrency tests.
4. Добавить `/settings` hub card поверх общих appliers.
5. Исправить status cache key и добавить owner-bound Stop поверх существующего cancellation path.
6. Уточнить reset notice и выполнить targeted, затем полный gateway test suite.
7. Разворачивать только новым immutable staging artifact после Luna/ops gates.

Rollback: вернуть предыдущий artifact. Новая preference table аддитивна и старый код её игнорирует; preferences можно сохранить для повторного rollout. Global config откатывается отдельным ops change.

## Open Questions

Нет вопросов, блокирующих implementation. Private Threaded Mode BotFather и announcement send не входят в этот change.
