## 1. Topic preferences и scopes

- [x] 1.1 Добавить schema/CRUD, versioned lane key и sanitization для `gateway_topic_preferences` с SQLite-тестами
- [x] 1.2 Подключить topic model resolution и общий model scope applier с precedence-тестами
- [x] 1.3 Подключить persistent topic reasoning и общий reasoning scope applier с restart/lifecycle-тестами
- [x] 1.4 Сделать Fast API session-scoped по умолчанию, добавить `--global`, отклонение `--topic` и очистку на session boundaries

## 2. Безопасные Telegram picker

- [x] 2.1 Перевести model picker на короткий nonce callback state с TTL и exact identity/message/thread/session binding
- [x] 2.2 Перевести generic choice picker на тот же контракт и ранний callback acknowledgement
- [x] 2.3 Добавить regression/security tests для concurrent, stale, foreign и oversized callback cases

## 3. Telegram `/settings`

- [x] 3.1 Зарегистрировать `/settings` и добавить текущую effective combination в command flow
- [x] 3.2 Реализовать topic-bound hub card Luna/Terra/Sol/All models, reasoning, Fast и Close через общие appliers
- [x] 3.3 Добавить tests независимого выбора, refresh/card state, полного model picker и topic isolation

## 4. Операции и session continuity

- [x] 4.1 Сделать status cache topic/session-aware и добавить owner-bound Stop поверх существующего cancellation path
- [x] 4.2 Добавить tests Stop callback lifecycle, stale generation и соседних топиков
- [x] 4.3 Исправить auto-reset notice и проверить `/new`/`/resume`: архив сохранён, topic preferences сохранены, Fast сброшен

## 5. Проверка

- [x] 5.1 Запустить targeted model/reasoning/fast/picker/settings/reset tests
- [x] 5.2 Запустить `scripts/run_tests.sh` в достаточном для gateway риска объёме и устранить regressions
- [x] 5.3 Выполнить `openspec validate add-gurra-telegram-modes-topics --strict` и зафиксировать evidence
