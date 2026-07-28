## 1. Шлюз Одобрения

- [x] 1.1 Получить явное одобрение базовой плановой версии OpenSpec перед любым
  изменением production-кода или тестов. Доказательство: baseline одобрен
  пользователем через `@best-step` 2026-07-28.
- [x] 1.2 Перед реализацией повторно подтвердить чистое рабочее дерево задачи,
  ветку `codex/fix-telegram-shared-room-free-response` и отсутствие действий с
  `live checkout`, конфигурацией, сервисами или DB. Доказательство: worktree,
  branch и base `9420a10079f8ca533e6026042d5264b81d660c3e` проверены; live
  действий нет.

## 2. Доказательства Через Тесты

- [x] 2.1 Найти точечные gateway-тесты для допуска общей комнаты,
  `free_response`, пассивного наблюдения и Telegram-политики для одного
  доверенного `principal`. Доказательство: использован
  `tests/gateway/test_telegram_group_gating.py`, точечная выборка 6 тестов.
- [x] 2.2 Если регрессия воспроизводится на текущем `main`, сначала добавить
  падающий тест или `mutation evidence`, показывающий, что точная общая комната
  из `telegram.free_response_chats` не вызывает `_handle_text_message` без
  упоминания или ответа. Доказательство: OLD production code дал 3 failed /
  3 passed; падения только dispatch/observation поведения.
- [x] 2.3 Покрыть фактический вызов `_handle_text_message` для пути
  `free_response` и отсутствие сохранения пассивного наблюдения для того же
  сообщения. Доказательство: batch runner вызывается 1 раз, passive store
  пустой, cleanup pending task детерминирован.
- [x] 2.4 Покрыть изоляцию двух тем: разные `trusted thread identity`/
  `session key` и неизменную нижележащую изоляцию памяти/доставки.
  Доказательство: темы 7 и 8 dispatch, `thread_id` разные, session keys
  разные.
- [x] 2.5 Покрыть случаи отказа и регрессии: другая комната, неизвестный или не
  общий чат, собственные сообщения, игнорируемые темы, запрещённая тема,
  комната только с упоминанием/ответом и существующий вызов по
  упоминанию/ответу. Доказательство: отрицательные gates и mention/reply-only
  shared room покрыты в точечной выборке.

## 3. Минимальная Реализация

- [x] 3.1 В `plugins/platforms/telegram/adapter.py` применить уже проверенный
  патч из двух веток после точной серверной проверки общей комнаты для одного
  доверенного `principal`. Доказательство: изменены только два adjacent guard
  участка после `shared_scope`.
- [x] 3.2 Ограничить `production`-изменение существующими проверками общей комнаты:
  групповой `telegram.free_response_chats` разрешает вызов без упоминания
  только для точной проверенной общей комнаты. Доказательство:
  `shared_scope is None` остаётся fail-closed, exact chat id даёт dispatch.
- [x] 3.3 Не добавлять `abstraction`, `config`, `dependency`, `refactor`,
  `schema`, `role`, `profile`, `tool`, `context field` или `migration`.
  Доказательство: production diff только в `adapter.py`, без новых helper/API.
- [x] 3.4 Подтвердить, что `free_response` остаётся только транспортным
  триггером и не меняет полномочия `authorization`, `membership`, `role`,
  `profile`, `capability` или `delivery`. Доказательство: auth/shared-scope
  gates выполняются раньше, adjacent auth/access sweep зелёный: 140 passed.

## 4. Проверка

- [x] 4.1 Запустить точечные тесты для изменённого поведения
  `free_response`/`group-gating` через `scripts/run_tests.sh`.
  Доказательство: focused green 6 passed / 0 failed через
  `scripts/run_tests.sh`.
- [x] 4.2 Запустить полный набор `group-gating` и соседний набор `auth`/`access`
  для одного доверенного `principal`, включая
  `tests/gateway/test_single_principal.py` и
  `tests/gateway/test_telegram_group_gating.py`. Доказательство:
  `test_telegram_group_gating.py`: 70 пройдено; соседние существующие файлы:
  140 пройдено / 0 провалено.
- [x] 4.3 Выполнить `openspec validate fix-telegram-shared-room-free-response --strict --no-interactive`.
  Доказательство: strict validate passed.
- [x] 4.4 Проверить `git status --short --branch` и убедиться, что нет
  `generated cache`/`bytecode`/`test duration artifacts`. Доказательство:
  сгенерированные `pyc`/`cache`/`test_durations` очищены; статус содержит
  только ожидаемые diff исходников и каталог OpenSpec.

## 5. Шлюзы Доставки

- [ ] 5.1 Подготовить отдельный PR этой задачи только после реализации, тестов,
  проверки и строгой валидации.
- [ ] 5.2 Перевод из `draft` в `ready` и `merge` выполнять только после проверки,
  актуального `diff`/`status` и подтверждённой готовности к `merge`.
- [x] 5.3 Не выполнять `live deploy`, `restart`, `private config edit`,
  `symlink switch`, операции с DB/сервисами или `live rollback` в этом
  изменении. Доказательство: live/config/services/DB не трогались.
- [x] 5.4 Зафиксировать план отката как `revert` `hotfix`-коммита;
  `live rollback` остаётся отдельным явным `live`-шлюзом. Доказательство:
  rollback plan: revert hotfix commit; live rollback требует отдельный gate.
