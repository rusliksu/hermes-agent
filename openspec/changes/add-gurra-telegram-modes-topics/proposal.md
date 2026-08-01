## Why

Пользователям Gurra нужен быстрый ответ на простые вопросы без потери доступа к глубокому режиму. Сейчас модель, уровень размышлений и Fast API переключаются разрозненно, часть выбора не переживает `/new`, а Telegram picker хранит состояние слишком широко и может конфликтовать между топиками одного чата.

## What Changes

- Добавить Telegram-команду `/settings` с отображением текущей комбинации и независимым выбором Luna, Terra, Sol, полного списка моделей, уровня размышлений и Fast API.
- Сохранять модель и уровень размышлений для полного Telegram topic lane (platform/chat/thread/user) так, чтобы выбор переживал restart gateway и `/new`.
- Добавить явные scope-флаги `--session`, `--topic`, `--global`; в Telegram без флага применять topic scope.
- Оставить Fast API ручным session-scoped режимом, который сбрасывается через `/new`.
- Защитить Telegram picker nonce-состоянием с TTL и привязкой к сообщению, чату, топику, пользователю и сессии; подтверждать callback немедленно.
- Уточнить сообщение автоматического сброса: активный контекст очищен, но прежняя сессия сохранена и доступна через `/resume`.
- Для продолжительных операций использовать одно статусное сообщение с возможностью остановки через кнопку, не размножая уведомления.

## Capabilities

### New Capabilities

- `telegram-topic-settings`: единая карточка настроек, scope-команды, долговечные topic preferences и session-scoped Fast API.
- `telegram-picker-integrity`: изоляция, авторизация, срок жизни и конкурентность Telegram picker callback.
- `telegram-operation-control`: компактный статус продолжительной операции и доступная пользователю остановка.
- `session-reset-continuity`: точная семантика reset/new и сообщение о сохранённой истории.

### Modified Capabilities

Нет.

## Impact

Затрагиваются slash-команды gateway, Telegram adapter, session/state storage, выбор runtime-модели и reasoning, тексты reset, а также связанные unit/integration tests. Секреты, BotFather и активный deploy-worktree не затрагиваются.

## Approval

Пользователь одобрил описанный ранее план командой `Implement the proposed plan.` 1 августа 2026 года. Любое материальное изменение observable behavior, scope, архитектуры или внешних гейтов требует нового одобрения.
